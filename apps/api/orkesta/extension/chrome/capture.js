// Se ejecuta SOLO cuando la persona abre la extensión o elige «Guardar en ORKESTA», y solo en la
// pestaña que está viendo: lee el perfil que tiene en pantalla (datos estructurados de la
// página, el título principal y el bloque que lo rodea, correos y teléfonos visibles) o lo que
// seleccionó. No recorre otras páginas ni pestañas. En LinkedIn no se ejecuta.
function orkestaCapture() {
  const clean = (s) => String(s || "").replace(/\s+/g, " ").trim();
  const meta = (name) =>
    clean(document.querySelector(`meta[property="${name}"], meta[name="${name}"]`)?.getAttribute("content"));
  const out = {
    selection: String(window.getSelection ? window.getSelection() : "").trim().slice(0, 2000),
    title: document.title.slice(0, 300),
    site: meta("og:site_name").slice(0, 200),
    host: location.hostname,
    url: location.href.slice(0, 600),
    sender: null,
    person: null,
    heading: "",
    block: "",
    emails: [],
    phones: [],
    links: [],
  };
  // 1) Datos estructurados (schema.org Person), los más confiables.
  const people = [];
  const walk = (node) => {
    if (!node || typeof node !== "object") return;
    if (Array.isArray(node)) return node.forEach(walk);
    const type = [].concat(node["@type"] || []).join(" ");
    if (/Person/i.test(type)) people.push(node);
    if (node["@graph"]) walk(node["@graph"]);
    if (node.mainEntity) walk(node.mainEntity);
  };
  document.querySelectorAll('script[type="application/ld+json"]').forEach((s) => {
    try { walk(JSON.parse(s.textContent || "")); } catch (e) {}
  });
  if (people.length) {
    const p = people[0];
    const org = [].concat(p.worksFor || p.affiliation || [])[0];
    const addr = [].concat(p.address || [])[0] || {};
    out.person = {
      name: clean(p.name), first: clean(p.givenName), last: clean(p.familyName),
      title: clean(p.jobTitle), company: clean(typeof org === "string" ? org : org && org.name),
      email: clean(p.email).replace(/^mailto:/i, ""), phone: clean(p.telephone),
      city: clean(addr.addressLocality), country: clean(addr.addressCountry && (addr.addressCountry.name || addr.addressCountry)),
    };
  }
  // 2) El perfil en pantalla: el título principal (si parece el nombre de una persona) y el
  // bloque que lo rodea. «Todos los contactos» o «Panel» no cuentan como nombre.
  const PARTICLES = /^(de|del|la|las|los|y|da|do|dos|van|von|di)$/i;
  const looksLikeName = (t) => {
    const w = t.split(" ");
    if (w.length < 2 || w.length > 6 || /\d|@|[:|/]/.test(t) || t.length > 60) return false;
    return w.every((x, i) => /^[A-ZÁÉÍÓÚÑÜ][\p{L}'’.-]*$/u.test(x) || (i > 0 && PARTICLES.test(x) && i < w.length - 1));
  };
  const visible = (el) => el && el.offsetParent !== null && clean(el.textContent).length > 1;
  const heading = [...document.querySelectorAll("h1, h2, [data-testid*=name i], [class*=name i]")]
    .filter(visible)
    .map((el) => ({ el, text: clean(el.textContent) }))
    .find((h) => looksLikeName(h.text));
  if (heading) {
    out.heading = heading.text;
    let box = heading.el;
    for (let i = 0; i < 4 && box.parentElement && clean(box.parentElement.innerText).length < 900; i++) box = box.parentElement;
    out.block = String(box.innerText || "").slice(0, 1500);
  }
  // 3) Correos, teléfonos y enlaces de perfil visibles.
  const scope = heading ? document.body : document.body;
  scope.querySelectorAll('a[href^="mailto:"]').forEach((a) => out.emails.push(clean(a.getAttribute("href")).slice(7).split("?")[0]));
  scope.querySelectorAll('a[href^="tel:"]').forEach((a) => out.phones.push(clean(a.getAttribute("href")).slice(4)));
  const text = (out.block || "") + " " + String(document.body.innerText || "").slice(0, 20000);
  (text.match(/[A-Z0-9._%+'-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi) || []).forEach((e) => out.emails.push(e));
  out.emails = [...new Set(out.emails.map((e) => e.toLowerCase()))].slice(0, 20);
  out.phones = [...new Set(out.phones)].slice(0, 5);
  document.querySelectorAll('a[href*="linkedin.com/in/"]').forEach((a) => out.links.push(a.href));
  out.links = [...new Set(out.links)].slice(0, 5);
  if (location.hostname === "mail.google.com") {
    const senders = document.querySelectorAll("h3 span[email], span.gD[email]");
    const last = senders[senders.length - 1];
    if (last) out.sender = { name: last.getAttribute("name") || last.textContent || "", email: last.getAttribute("email") || "" };
  }
  return out;
}

// Perfil de LinkedIn abierto: SOLO cuando la persona toca «Agregar a ORKESTA», y solo el
// encabezado visible del perfil (nombre, titular, empresa actual y ubicación). No navega, no
// abre otras secciones, no recorre listas ni hace acciones en LinkedIn.
function orkestaLinkedIn() {
  const clean = (s) => String(s || "").replace(/\s+/g, " ").trim();
  const h1 = document.querySelector("main h1") || document.querySelector("h1");
  const out = { name: clean(h1 && h1.innerText), title: "", company: "", location: "" };
  if (!h1) return out;
  const top = h1.closest("section") || h1.parentElement.parentElement.parentElement;
  const headline = top.querySelector(".text-body-medium");
  out.title = clean(headline && headline.innerText);
  const loc = top.querySelector("span.text-body-small.inline, .pv-text-details__left-panel .text-body-small");
  out.location = clean(loc && loc.innerText);
  const firm = top.querySelector('[aria-label^="Empresa actual"], [aria-label^="Current company"], [aria-label*="empresa actual" i]');
  if (firm) out.company = clean(firm.getAttribute("aria-label").split(":").slice(1).join(":")).replace(/\.\s.*$/, "");
  if (!out.company) {
    const item = top.querySelector('ul li button img[alt], ul li a img[alt]');
    const li = item && item.closest("li");
    if (li) out.company = clean(li.innerText).split("\n")[0];
  }
  const lines = String(top.innerText || "").split("\n").map(clean).filter(Boolean);
  if (!out.location) out.location = lines.find((l) => /,/.test(l) && l.length < 80 && l !== out.title && !/contact|seguidor|follower/i.test(l)) || "";
  out.location = out.location.replace(/\s*·.*$/, "");
  if (!out.company && out.title) {
    const m = out.title.match(/\s(?:at|en|@|\||-)\s(?!.*\s(?:at|en|@|\||-)\s)(.+)$/i);
    if (m) out.company = clean(m[1]);
  }
  return out;
}
