const API = "https://orkestaapp.com";
const $ = (id) => document.getElementById(id);
const show = (id) => ["connect", "linkedin", "form"].forEach((s) => ($(s).hidden = s !== id));
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
const SKIP_HOSTS = /(google|gmail|outlook|live|yahoo|facebook|instagram|x\.com|twitter|youtube|wikipedia|lemlist|hubspot|salesforce|apollo|pipedrive|zoho)\./i;
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

async function capture(tab) {
  const stored = (await chrome.storage.session.get("pending")).pending;
  if (stored && Date.now() - stored.at < 60_000) {
    await chrome.storage.session.remove("pending");
    return stored;
  }
  if (!tab || !tab.id) return {};
  try {
    const [out] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: orkestaCapture });
    return (out && out.result) || {};
  } catch (e) {
    return { url: tab.url || "", title: tab.title || "" };
  }
}

function render(values) {
  const name = `${values.first_name} ${values.last_name}`.trim();
  $("sName").textContent = name || "No encontré el nombre";
  $("sName").className = name ? "name" : "name empty";
  $("sTitle").textContent = [values.job_title, values.company].filter(Boolean).join(" · ") || "Sin cargo ni empresa";
  $("sEmail").textContent = values.email || (values.company ? "Sin correo: ORKESTA lo buscará y lo verificará" : "Sin correo");
  $("sSource").textContent = values.source ? `Leído de ${values.source}.` : "";
}

async function main() {
  const { token } = await chrome.storage.local.get("token");
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
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const pending = (await chrome.storage.session.get("pending")).pending;
  const fresh = pending && Date.now() - pending.at < 60_000;
  const url = (fresh && pending.url) || (tab && tab.url) || "";
  let host = "";
  try { host = new URL(url).hostname; } catch (e) {}
  if (/(^|\.)linkedin\.com$/i.test(host)) {
    show("linkedin");
    $("liMore").onclick = () => { $("liFields").hidden = false; $("liMore").hidden = true; };
    if (!/linkedin\.com\/in\//i.test(url)) {
      $("liBtn").hidden = true;
      $("liMore").hidden = true;
      $("liMsg").textContent = "Abre el perfil de una persona (linkedin.com/in/…) para agregarlo.";
      return;
    }
    $("liBtn").onclick = async () => {
      $("liBtn").disabled = true;
      $("liMsg").textContent = "Buscando a la persona…";
      $("liMsg").className = "msg";
      let seen = { name: "", title: "", company: "", location: "" };
      try {
        const [res] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: orkestaLinkedIn });
        if (res && res.result) seen = res.result;
      } catch (e) {}
      if (seen.name) {
        $("liSummary").hidden = false;
        $("liName").textContent = seen.name;
        $("liTitle").textContent = [seen.title, seen.company].filter(Boolean).join(" · ");
        $("liPlace").textContent = seen.location;
        $("liHint").hidden = true;
      }
      try {
        const r = await call("/api/ext/linkedin", token, {
          url,
          company: $("liCompany").value.trim() || seen.company,
          country: $("liCountry").value.toUpperCase(),
          name: seen.name, title: seen.title, location: seen.location,
        });
        $("liMsg").textContent = r.created || r.updated
          ? (r.with_email ? "Guardado con su correo." : r.enrichment_id ? "Guardado. ORKESTA está buscando su correo." : "Guardado en Contactos.")
          : r.existing ? "Ya estaba en tus contactos." : "No se pudo agregar.";
        $("liMsg").className = "msg ok";
      } catch (e) {
        $("liMsg").textContent = e.message;
        $("liMsg").className = "msg bad";
      } finally {
        $("liBtn").disabled = false;
      }
    };
    return;
  }
  const page = await capture(tab);
  const values = fromPage(page);
  for (const k of FIELDS) if ($(k)) $(k).value = values[k] || "";
  render(values);
  show("form");
  const found = values.first_name && values.last_name;
  if (!found) {
    $("fields").hidden = false;
    $("editBtn").hidden = true;
    $("hint").textContent = "No encontré un perfil en esta página. Selecciona con el mouse el nombre, cargo y empresa de la persona y vuelve a abrir la extensión (o clic derecho → «Guardar en ORKESTA»).";
  }
  $("editBtn").onclick = () => { $("fields").hidden = false; $("editBtn").hidden = true; };
  for (const k of FIELDS) if ($(k)) $(k).oninput = () => render({ ...values, ...Object.fromEntries(FIELDS.map((f) => [f, $(f).value])) });
  $("saveBtn").onclick = async () => {
    $("saveBtn").disabled = true;
    $("saveMsg").textContent = "";
    const body = { source_url: page.url || url, enrich: $("enrich").checked };
    for (const k of FIELDS) body[k] = $(k).value.trim();
    try {
      const r = await call("/api/ext/contacts", token, body);
      $("saveMsg").textContent = r.email ? `Guardado: ${r.name}.` : r.enrichment_id ? `Guardado: ${r.name}. ORKESTA está buscando su correo.` : `Guardado: ${r.name}.`;
      $("saveMsg").className = "msg ok";
    } catch (e) {
      $("saveMsg").textContent = e.message;
      $("saveMsg").className = "msg bad";
      $("fields").hidden = false;
    } finally {
      $("saveBtn").disabled = false;
    }
  };
}

$("connectBtn").onclick = () => {
  chrome.tabs.create({ url: `${API}/app/conectar-extension` });
  window.close();
};
chrome.storage.onChanged.addListener((changes) => {
  if (changes.token && changes.token.newValue) $("ver").textContent = `v${chrome.runtime.getManifest().version}`;
main();
});
$("logout").onclick = async (e) => {
  e.preventDefault();
  const { token } = await chrome.storage.local.get("token");
  if (token) await fetch(`${API}/api/ext/logout`, { method: "POST", headers: { Authorization: `Bearer ${token}` } }).catch(() => {});
  await chrome.storage.local.remove("token");
  $("who").textContent = "";
  $("logout").hidden = true;
  show("connect");
};
$("ver").textContent = `v${chrome.runtime.getManifest().version}`;
main();
