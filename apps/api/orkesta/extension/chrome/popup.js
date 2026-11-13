const API = "https://orkestaapp.com";
const $ = (id) => document.getElementById(id);
const show = (id) => ["connect", "linkedin", "form"].forEach((s) => ($(s).hidden = s !== id));

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

// --- Lo seleccionado → campos del contacto (la persona revisa antes de guardar) ---------------
const EMAIL = /[A-Z0-9._%+'-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i;
const PHONE = /\+?\d[\d\s().-]{7,}\d/;
const TITLE = /\b(ceo|cfo|cio|cto|cmo|coo|cdo|ciso|chief|head|director|directora|gerente|jefe|jefa|vp|vice|presidente|presidenta|manager|lead|líder|lider|coordinador|coordinadora|founder|fundador|fundadora|socio|socia|owner|dueño|analista|especialista|ejecutivo|ejecutiva)\b/i;
const SKIP_HOSTS = /(google|gmail|outlook|live|yahoo|facebook|instagram|x\.com|twitter|youtube|wikipedia)\./i;

function fromPage(page) {
  const out = { first_name: "", last_name: "", job_title: "", company: "", email: "", phone: "", website: "", country: "" };
  const text = (page.selection || "").replace(/ /g, " ");
  const parts = text.split(/\n|\s[·|•]\s|\s-\s|\s—\s|\t/).map((s) => s.trim()).filter(Boolean);
  const email = text.match(EMAIL);
  if (email) out.email = email[0].toLowerCase();
  const phone = text.match(PHONE);
  if (phone) out.phone = phone[0].trim();
  for (const p of parts) {
    if (EMAIL.test(p) || PHONE.test(p)) continue;
    if (!out.job_title && TITLE.test(p) && p.length < 120) {
      const at = p.split(/\s(?:at|en|@)\s/i);
      out.job_title = at[0].trim();
      if (at[1] && !out.company) out.company = at[1].trim();
      continue;
    }
    const words = p.split(/\s+/);
    if (!out.first_name && words.length >= 2 && words.length <= 5 && /^[A-ZÁÉÍÓÚÑ]/.test(p) && !/\d/.test(p)) {
      out.first_name = words[0];
      out.last_name = words.slice(1).join(" ");
      continue;
    }
    if (!out.company && p.length < 80) out.company = p;
  }
  if (page.sender && page.sender.email) {
    out.email = out.email || page.sender.email.toLowerCase();
    if (!out.first_name && page.sender.name) {
      const [f, ...l] = page.sender.name.trim().split(/\s+/);
      out.first_name = f;
      out.last_name = l.join(" ");
    }
  }
  const host = (page.host || "").replace(/^www\./, "");
  if (host && !SKIP_HOSTS.test(host)) {
    out.website = host;
    if (!out.company) out.company = (page.site || (page.title || "").split(/\s[|–—-]\s/).pop() || "").trim().slice(0, 80);
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
    // orkestaCapture viene de capture.js (cargado en esta ventana) y se ejecuta en la pestaña.
    const [out] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: orkestaCapture });
    return (out && out.result) || {};
  } catch (e) {
    return { url: tab.url || "", title: tab.title || "" };
  }
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
    if (/linkedin\.com\/in\//i.test(url)) {
      show("linkedin");
      $("liBtn").onclick = async () => {
        $("liBtn").disabled = true;
        try {
          const r = await call("/api/ext/linkedin", token, { url, company: $("liCompany").value, country: $("liCountry").value.toUpperCase() });
          $("liMsg").textContent = r.created
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
    } else {
      show("linkedin");
      $("liBtn").hidden = true;
      $("liMsg").textContent = "Abre el perfil de una persona (linkedin.com/in/…) para agregarlo por su dirección.";
    }
    return;
  }
  const page = await capture(tab);
  const values = fromPage(page);
  for (const [k, v] of Object.entries(values)) if ($(k)) $(k).value = v || "";
  if (page.selection || (page.sender && page.sender.email)) $("hint").textContent = "Revisa los datos antes de guardar.";
  show("form");
  $("saveBtn").onclick = async () => {
    $("saveBtn").disabled = true;
    $("saveMsg").textContent = "";
    const body = { source_url: page.url || url, enrich: $("enrich").checked };
    for (const k of ["first_name", "last_name", "job_title", "company", "email", "phone", "website", "country"]) body[k] = $(k).value.trim();
    try {
      const r = await call("/api/ext/contacts", token, body);
      $("saveMsg").textContent = r.email ? `Guardado: ${r.name}.` : r.enrichment_id ? `Guardado: ${r.name}. ORKESTA está buscando su correo.` : `Guardado: ${r.name}.`;
      $("saveMsg").className = "msg ok";
    } catch (e) {
      $("saveMsg").textContent = e.message;
      $("saveMsg").className = "msg bad";
    } finally {
      $("saveBtn").disabled = false;
    }
  };
}

$("connectBtn").onclick = () => {
  chrome.tabs.create({ url: `${API}/app/conectar-extension` });
  window.close();
};
// Si la conexión termina mientras la ventana está abierta, se actualiza sola.
chrome.storage.onChanged.addListener((changes) => {
  if (changes.token && changes.token.newValue) main();
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
main();
