const API = "https://orkestaapp.com";
const $ = (id) => document.getElementById(id);
const show = (id) => ["connect", "loading", "linkedin", "form", "list"].forEach((s) => ($(s).hidden = s !== id));
const FIELDS = ["first_name", "last_name", "job_title", "company", "email", "phone", "website", "country"];

async function call(path, token, body) {
  const res = await fetch(API + path, {
    method: body ? "POST" : "GET",
    headers: { Authorization: `Bearer ${token}`, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || "No se pudo conectar con ORKESTA.");
  return data;
}

// --- Leer el perfil que está en pantalla --------------------------------------------------------
const EMAIL = /[A-Z0-9._%+'-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i;
const PHONE = /\+?\d[\d\s().-]{7,}\d/;
const TITLE = /\b(ceo|cfo|cio|cto|cmo|coo|cdo|ciso|chief|head|director|directora|gerente|jefe|jefa|vp|vice ?president|presidente|presidenta|manager|lead|líder|lider|coordinador|coordinadora|founder|co-?founder|fundador|fundadora|socio|socia|partner|owner|dueño|analista|especialista|ejecutivo|ejecutiva|consultor|consultora|ingeniero|ingeniera|abogado|abogada|asesor|asesora|subgerente|supervisor|supervisora|account|sales|marketing|officer)\b/i;
const SKIP_HOSTS = /(google|gmail|outlook|live|yahoo|facebook|instagram|x\.com|twitter|youtube|wikipedia|lemlist|hubspot|salesforce|apollo|prospeo|lusha|wiza|pipedrive|zoho)\./i;
const AT = /\s(?:at|en|@)\s(?!.*\s(?:at|en|@)\s)(.+)$/i;
const NOISE = /^(contact|contacto|perfil|profile|email|correo|teléfono|telefono|phone|empresa|company|cargo|title|linkedin|ver más|editar|edit|follow|seguir)$/i;

function splitName(full) {
  const words = full.replace(/,.*$/, "").replace(/\b(phd|mba|msc|dr|dra|ing|lic)\.?$/i, "").trim().split(/\s+/);
  if (words.length < 2) return [words[0] || "", ""];
  if (words.length >= 4) return [words.slice(0, 2).join(" "), words.slice(2).join(" ")];
  return [words[0], words.slice(1).join(" ")];
}

function fromLines(text, out) {
  const parts = text.split(/\n|\s[·|•]\s|\s-\s|\s—\s|\t/).map((s) => s.trim()).filter((s) => s && !NOISE.test(s));
  for (const p of parts) {
    if (!out.email && EMAIL.test(p)) { out.email = p.match(EMAIL)[0].toLowerCase(); continue; }
    if (!out.phone && PHONE.test(p) && !/[a-z]{4}/i.test(p)) { out.phone = p.match(PHONE)[0].trim(); continue; }
    if (!out.job_title && TITLE.test(p) && p.length < 140) {
      const at = p.match(AT);
      out.job_title = (at ? p.slice(0, at.index) : p).trim();
      if (at && !out.company) out.company = at[1].trim().slice(0, 120);
      continue;
    }
    const words = p.split(/\s+/);
    if (!out.first_name && words.length >= 2 && words.length <= 6 && /^[A-ZÁÉÍÓÚÑ]/.test(p) && !/\d|@/.test(p) && !TITLE.test(p)) {
      [out.first_name, out.last_name] = splitName(p);
      continue;
    }
    if (!out.company && out.first_name && out.job_title && p.length < 80 && !EMAIL.test(p)) out.company = p;
  }
}

function fromPage(page) {
  const out = { first_name: "", last_name: "", job_title: "", company: "", email: "", phone: "", website: "", country: "", source: "" };
  const p = page.person;
  if (page.selection) {
    fromLines(page.selection, out);
    out.source = "lo que seleccionaste";
  } else if (p && (p.name || p.first)) {
    [out.first_name, out.last_name] = p.first ? [p.first, p.last] : splitName(p.name);
    Object.assign(out, { job_title: p.title, company: p.company, email: p.email, phone: p.phone, country: p.country });
    out.source = "los datos del perfil de la página";
  } else if (page.block || page.heading) {
    if (page.heading) [out.first_name, out.last_name] = splitName(page.heading);
    fromLines(page.block || "", out);
    out.source = "el perfil que tienes abierto";
  }
  if (page.sender && page.sender.email) {
    out.email = out.email || page.sender.email.toLowerCase();
    if (!out.first_name && page.sender.name) [out.first_name, out.last_name] = splitName(page.sender.name.trim());
    out.source = out.source || "el remitente del correo";
  }
  // El correo de la persona: el que se parece a su nombre (no el de soporte del sitio).
  if (!out.email && page.emails && page.emails.length) {
    const first = (out.first_name || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").split(" ")[0];
    const last = (out.last_name || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").split(" ")[0];
    const match = page.emails.find((e) => (first && e.includes(first)) || (last && last.length > 2 && e.includes(last)));
    if (match) out.email = match;
  }
  if (!out.phone && page.phones && page.phones.length === 1) out.phone = page.phones[0];
  const host = (page.host || "").replace(/^www\./, "");
  // En el sitio de la propia empresa (no en un CRM, red o noticia), ese sitio es su web.
  if (host && !SKIP_HOSTS.test(host) && !out.company) {
    out.website = host;
    out.company = (page.site || (page.title || "").split(/\s[|–—-]\s/).pop() || "").trim().slice(0, 80);
  }
  if (out.email && !out.website) {
    const domain = out.email.split("@")[1];
    if (!/(gmail|hotmail|outlook|yahoo|live|icloud)\./.test(domain)) out.website = domain;
  }
  return out;
}

function render(values) {
  const name = `${values.first_name} ${values.last_name}`.trim();
  $("sName").textContent = name || "No encontré el nombre";
  $("sName").className = name ? "name" : "name empty";
  $("sTitle").textContent = [values.job_title, values.company].filter(Boolean).join(" · ") || "Sin cargo ni empresa";
  $("sEmail").textContent = values.email || (values.company ? "Sin correo: ORKESTA lo buscará y lo verificará" : "Sin correo");
  $("sSource").textContent = values.source ? `Leído de ${values.source}.` : "";
}

// --- De dónde sale lo que está en pantalla ----------------------------------------------------
// En el panel flotante, la página se lee desde el panel (panel.js) y llega por mensaje. En la
// ventana de respaldo (páginas del navegador) no hay página que leer.
const PANEL = new URLSearchParams(location.search).has("panel");
const NONCE = location.hash.slice(1);
const PARENT = "*"; // el panel verifica la ventana y la marca; aquí no hay datos sensibles
function askPage() {
  if (PANEL) window.parent.postMessage({ type: "orkesta-read", nonce: NONCE }, PARENT);
}
let token = "";
let current = null;
let trusted = false;

async function main() {
  if (PANEL && !trusted) {
    const ok = await chrome.runtime.sendMessage({ type: "panel-check", nonce: NONCE }).catch(() => null);
    if (!ok || !ok.ok) {
      document.body.textContent = "";
      return;
    }
    trusted = true;
  }
  ({ token } = await chrome.storage.local.get("token"));
  if (!token) {
    show("connect");
    return;
  }
  let me;
  try {
    me = await call("/api/ext/me", token);
  } catch (e) {
    await chrome.storage.local.remove("token");
    $("connectMsg").textContent = "Tu sesión de la extensión terminó. Vuelve a conectarla.";
    $("connectMsg").className = "msg bad";
    show("connect");
    return;
  }
  $("who").textContent = `${me.name} · ${me.company}`;
  $("logout").hidden = false;
  $("reread").hidden = !PANEL;
  if (PANEL) {
    show("loading");
    askPage();
  } else {
    showPage({ url: "", host: "", list: { people: [] } });
  }
}

function showPage(page) {
  current = page;
  const url = page.url || "";
  const people = (page.list && page.list.people) || [];
  if (people.length >= 2) return showList(page, people);
  if (page.linkedin) return linkedinProfile(page.linkedin, url);
  if (page.salesnav) return salesNav(page.salesnav, url);
  if (/(^|\.)linkedin\.com$/i.test(page.host || "")) {
    show("linkedin");
    $("liSummary").hidden = true;
    $("liBtn").hidden = true;
    $("liMore").hidden = true;
    $("liFields").hidden = true;
    $("liHint").hidden = false;
    $("liHint").textContent = "Abre el perfil de una persona o una búsqueda de personas y toca «Leer de nuevo».";
    $("liMsg").textContent = "";
    return;
  }
  if (people.length === 1 && !(page.page && (page.page.heading || page.page.person || page.page.selection))) {
    const [first, last] = splitName(people[0].name);
    return showForm({ first_name: first, last_name: last, job_title: people[0].title, company: people[0].company,
      email: people[0].email, phone: people[0].phone || "", website: "", country: people[0].location, source: "la lista en pantalla" }, url);
  }
  return showForm(fromPage(page.page || {}), url);
}

function savedText(r) {
  return r.email ? "Guardado con su correo." : r.enrichment_id ? "Guardado. ORKESTA está buscando su correo." : "Guardado en Contactos.";
}
function msg(id, text, kind) {
  $(id).textContent = text;
  $(id).className = `msg ${kind || ""}`;
}

// --- Perfil de LinkedIn ---------------------------------------------------------------------------
function linkedinProfile(seen, url) {
  show("linkedin");
  $("liBtn").hidden = false;
  $("liMore").hidden = false;
  $("liFields").hidden = true;
  msg("liMsg", "");
  $("liMore").onclick = () => { $("liFields").hidden = false; $("liMore").hidden = true; };
  if (seen.name) {
    $("liSummary").hidden = false;
    $("liName").textContent = seen.name;
    $("liTitle").textContent = [seen.title, seen.company].filter(Boolean).join(" · ") || "Sin cargo ni empresa visibles";
    $("liPlace").textContent = seen.location;
    $("liHint").hidden = true;
    $("liCompany").value = seen.company || "";
  } else {
    $("liSummary").hidden = true;
    $("liHint").hidden = false;
    $("liHint").textContent = "No pude leer el perfil (espera a que cargue y toca «Leer de nuevo»). Igual puedes agregarlo: ORKESTA lo buscará por su dirección.";
  }
  $("liBtn").onclick = async () => {
    $("liBtn").disabled = true;
    msg("liMsg", "Guardando…");
    try {
      const r = await call("/api/ext/linkedin", token, {
        url, company: $("liCompany").value.trim(), country: $("liCountry").value.toUpperCase(),
        name: seen.name, title: seen.title, location: seen.location,
      });
      msg("liMsg", r.created || r.updated ? savedText({ email: r.with_email, enrichment_id: r.enrichment_id })
        : r.existing ? "Ya estaba en tus contactos." : "No se pudo agregar.", "ok");
    } catch (e) {
      msg("liMsg", e.message, "bad");
    } finally {
      $("liBtn").disabled = false;
    }
  };
}

// --- Ficha abierta de Sales Navigator ---------------------------------------------------------------
function salesNav(seen, url) {
  show("linkedin");
  $("liFields").hidden = true;
  msg("liMsg", "");
  $("liMore").onclick = () => { $("liFields").hidden = false; $("liMore").hidden = true; };
  if (!seen.name) {
    $("liSummary").hidden = true;
    $("liBtn").hidden = true;
    $("liMore").hidden = true;
    $("liHint").hidden = false;
    $("liHint").textContent = "Abre la ficha de un posible cliente o una búsqueda de leads y toca «Leer de nuevo».";
    return;
  }
  $("liBtn").hidden = false;
  $("liMore").hidden = false;
  $("liSummary").hidden = false;
  $("liName").textContent = seen.name;
  $("liTitle").textContent = [seen.title, seen.company].filter(Boolean).join(" · ") || "Sin cargo ni empresa visibles";
  $("liPlace").textContent = seen.location;
  $("liHint").hidden = true;
  $("liCompany").value = seen.company || "";
  $("liBtn").onclick = async () => {
    $("liBtn").disabled = true;
    msg("liMsg", "Guardando…");
    const company = $("liCompany").value.trim();
    try {
      let r;
      if (seen.profile) {
        r = await call("/api/ext/linkedin", token, { url: seen.profile, company, country: $("liCountry").value.toUpperCase(),
          name: seen.name, title: seen.title, location: seen.location });
        r = { ok: r.created || r.updated, email: r.with_email, enrichment_id: r.enrichment_id, existing: r.existing };
      } else {
        const [first, last] = splitName(seen.name);
        const res = await call("/api/ext/contacts/bulk", token, { source_url: seen.lead || url, enrich: true,
          people: [{ first_name: first, last_name: last, job_title: seen.title, company, location: seen.location }] });
        r = { ok: res.created > 0, existing: res.existing > 0, enrichment_id: res.enrichment_id, error: res.errors[0] };
      }
      if (r.error) throw new Error(r.error);
      msg("liMsg", r.ok ? savedText(r) : r.existing ? "Ya estaba en tus contactos." : "No se pudo agregar.", "ok");
    } catch (e) {
      msg("liMsg", e.message, "bad");
    } finally {
      $("liBtn").disabled = false;
    }
  };
}

// --- Un contacto en cualquier página -----------------------------------------------------------------
function showForm(values, url) {
  for (const k of FIELDS) if ($(k)) $(k).value = values[k] || "";
  render(values);
  show("form");
  msg("saveMsg", "");
  const found = values.first_name && values.last_name;
  $("fields").hidden = !!found;
  $("editBtn").hidden = !found;
  $("hint").textContent = found ? "" : "No encontré un perfil en esta página. Selecciona con el mouse el nombre, cargo y empresa de la persona y toca «Leer de nuevo» (o clic derecho → «Guardar en ORKESTA»).";
  $("editBtn").onclick = () => { $("fields").hidden = false; $("editBtn").hidden = true; };
  for (const k of FIELDS) if ($(k)) $(k).oninput = () => render({ ...values, ...Object.fromEntries(FIELDS.map((f) => [f, $(f).value])) });
  $("saveBtn").onclick = async () => {
    $("saveBtn").disabled = true;
    msg("saveMsg", "");
    const body = { source_url: url, enrich: $("enrich").checked };
    for (const k of FIELDS) body[k] = $(k).value.trim();
    try {
      const r = await call("/api/ext/contacts", token, body);
      msg("saveMsg", r.email ? `Guardado: ${r.name}.` : r.enrichment_id ? `Guardado: ${r.name}. ORKESTA está buscando su correo.` : `Guardado: ${r.name}.`, "ok");
    } catch (e) {
      msg("saveMsg", e.message, "bad");
      $("fields").hidden = false;
    } finally {
      $("saveBtn").disabled = false;
    }
  };
}

// --- Varias personas en pantalla (búsqueda de leads, búsqueda de LinkedIn, tablas) -----------------
const MAX_SAVE = 100;
const SOURCES = { salesnav: "la búsqueda de Sales Navigator", linkedin: "la búsqueda de LinkedIn", table: "la tabla de esta página" };

function toPerson(p) {
  const [first, last] = splitName(p.name);
  return { first_name: first, last_name: last, job_title: p.title || "", company: p.company || "", email: p.email || "",
    phone: p.phone || "", location: p.location || "", linkedin_url: p.url || "" };
}

function showList(page, people) {
  show("list");
  msg("lMsg", "");
  $("lTitle").textContent = `${people.length} personas en pantalla`;
  $("lSource").textContent = `Leídas de ${SOURCES[page.list.kind] || "esta página"}. Para ver más, baja o pasa de página y toca «Leer de nuevo».`;
  const rows = $("lRows");
  rows.textContent = "";
  const boxes = people.map((p, i) => {
    const row = document.createElement("label");
    row.className = "person";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = true;
    box.dataset.i = String(i);
    const info = document.createElement("span");
    info.className = "info";
    const name = document.createElement("span");
    name.className = "pname";
    name.textContent = p.name;
    const line = document.createElement("span");
    line.className = "pline";
    line.textContent = [p.title, p.company].filter(Boolean).join(" · ") || "Sin cargo ni empresa";
    const more = document.createElement("span");
    more.className = "pline muted";
    more.textContent = [p.email, p.location].filter(Boolean).join(" · ");
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.hidden = true;
    info.append(name, line, more);
    row.append(box, info, tag);
    rows.append(row);
    box.onchange = count;
    return { box, tag, row };
  });
  function count() {
    const n = boxes.filter((b) => b.box.checked).length;
    $("lSave").textContent = `Guardar seleccionados (${n})`;
    $("lSave").disabled = n === 0;
    $("lAll").checked = n === boxes.length;
  }
  $("lAll").onchange = () => { boxes.forEach((b) => (b.box.checked = $("lAll").checked)); count(); };
  count();
  // Quién ya está en Contactos: se marca y se desmarca para no repetirlo.
  const list = people.slice(0, MAX_SAVE * 3).map(toPerson);
  (async () => {
    for (let i = 0; i < list.length; i += MAX_SAVE) {
      try {
        const r = await call("/api/ext/contacts/check", token, { people: list.slice(i, i + MAX_SAVE) });
        r.exists.forEach((yes, j) => {
          const b = boxes[i + j];
          if (yes && b) { b.tag.hidden = false; b.tag.textContent = "Ya está"; b.box.checked = false; b.row.classList.add("dim"); }
        });
      } catch (e) {}
    }
    if (current && current.list && current.list.people === people) count();
  })();
  const save = async (only) => {
    const chosen = boxes.filter((b) => !only || b.box.checked).map((b) => Number(b.box.dataset.i));
    if (!chosen.length) return;
    const batch = chosen.slice(0, MAX_SAVE);
    $("lSave").disabled = $("lSaveAll").disabled = true;
    msg("lMsg", `Guardando ${batch.length}…`);
    try {
      const r = await call("/api/ext/contacts/bulk", token, { source_url: page.url, enrich: $("lEnrich").checked,
        people: batch.map((i) => toPerson(people[i])) });
      for (const i of batch) {
        const b = boxes[i];
        b.box.checked = false;
        b.tag.hidden = false;
        b.tag.textContent = "Guardado";
        b.row.classList.add("dim");
      }
      const parts = [`${r.created} guardado(s)`];
      if (r.existing) parts.push(`${r.existing} ya estaba(n)`);
      if (r.pending && r.enrichment_id) parts.push(`ORKESTA busca el correo de ${r.pending}`);
      else if (r.pending) parts.push(`${r.pending} sin correo: búscalos luego en Enriquecer`);
      if (chosen.length > MAX_SAVE) parts.push(`guarda de a ${MAX_SAVE}: quedan ${chosen.length - MAX_SAVE} por guardar`);
      msg("lMsg", parts.join(" · ") + ".", r.errors.length ? "bad" : "ok");
      if (r.errors.length) $("lMsg").textContent += ` No se guardaron: ${r.errors.join("; ")}`;
    } catch (e) {
      msg("lMsg", e.message, "bad");
    } finally {
      $("lSaveAll").disabled = false;
      count();
    }
  };
  $("lSave").onclick = () => save(true);
  $("lSaveAll").onclick = () => save(false);
}

window.addEventListener("message", (e) => {
  const d = e.data || {};
  if (!PANEL || !trusted || e.source !== window.parent || d.type !== "orkesta-page" || d.nonce !== NONCE || !token) return;
  showPage(d.page || {});
});
$("reread").onclick = (e) => {
  e.preventDefault();
  show("loading");
  askPage();
};
$("connectBtn").onclick = () => {
  chrome.tabs.create({ url: `${API}/app/conectar-extension` });
  if (!PANEL) window.close();
};
chrome.storage.onChanged.addListener((changes) => {
  if (changes.token && changes.token.newValue) main();
});
$("logout").onclick = async (e) => {
  e.preventDefault();
  const stored = (await chrome.storage.local.get("token")).token;
  if (stored) await fetch(`${API}/api/ext/logout`, { method: "POST", headers: { Authorization: `Bearer ${stored}` } }).catch(() => {});
  await chrome.storage.local.remove("token");
  token = "";
  $("who").textContent = "";
  $("logout").hidden = true;
  $("reread").hidden = true;
  show("connect");
};
if (PANEL) document.body.classList.add("panel");
$("ver").textContent = `v${chrome.runtime.getManifest().version}`;
main();
