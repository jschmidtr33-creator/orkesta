// Lectores de la página. Solo se ejecutan en la pestaña que la persona está viendo, cuando abre
// el panel de ORKESTA o toca «Leer de nuevo»: leen lo que está en pantalla (un perfil o una lista
// de personas). No navegan, no hacen scroll, no abren otras páginas ni pestañas y no hacen nada
// en el sitio.
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

// Perfil de LinkedIn abierto: SOLO cuando la persona abre la extensión en ese perfil, y solo el
// encabezado visible: nombre, el titular que está justo debajo (cargo), la empresa actual (la del
// logo al lado del nombre) y la ubicación. No navega, no abre otras secciones, no recorre listas
// ni hace acciones en LinkedIn.
function orkestaLinkedIn() {
  const clean = (s) => String(s || "").replace(/\s+/g, " ").trim();
  const NOISE = /lemlist|apollo|lusha|wiza|contactos|connections|seguidores|followers|información de contacto|contact info|^más$|^more$|mensaje|message|conectar|connect|seguir|follow|tengo interés|open to|añadir sección|add section|concertar|agendar|recursos|resources|guardar en|save in|sales navigator|en común|in common|^[•·]|^\W*\d+\s*(º|°|er|nd|rd|th)\b|^\W*(1er|2º|3er|1st|2nd|3rd)\W*$|\b(1er|2º|3er|1st|2nd|3rd)\b|^ver |^see |verificad|verified|he\/him|she\/her|ella\/|él\/|premium|^\d+$|^…|^\.\.\./i;
  const SCHOOL = /universidad|university|universit|colegio|school|escuela|instituto|institute|college|academia|academy|politécnico|politecnico|tecnológico|tecnologico|business school|maestr|mba\b/i;
  const COUNTRY = /colombia|méxico|mexico|chile|perú|peru|argentina|ecuador|panamá|panama|costa rica|guatemala|uruguay|paraguay|bolivia|venezuela|dominicana|puerto rico|honduras|el salvador|nicaragua|cuba|brasil|brazil|españa|spain|estados unidos|united states|canadá|canada|portugal|reino unido|united kingdom|metropolitan|metropolitana|alrededores|greater|área de|area\b/i;
  const DURATION = /^\d+\s*(año|años|mes|meses|yr|yrs|year|years|mo|mos|month|months)\b|\b(actualidad|present)\b/i;
  const fromTitle = clean(document.title.replace(/^\(\d+\)\s*/, "").split("|")[0]);
  const main = document.querySelector("main") || document.body;
  // El nombre: el h1 o, si LinkedIn ya no usa h1, el elemento más chico cuyo texto es el nombre.
  let nameEl = [...main.querySelectorAll("h1")].find((h) => clean(h.innerText).length > 2);
  if (!nameEl && fromTitle) {
    nameEl = [...main.querySelectorAll("h1, h2, h3, span, div, p, a")]
      .filter((el) => clean(el.innerText) === fromTitle)
      .find((el) => ![...el.children].some((c) => clean(c.innerText) === fromTitle));
  }
  const name = clean(nameEl && nameEl.innerText) || fromTitle;
  const out = { name, title: "", company: "", location: "" };
  if (!nameEl) return out;
  // El encabezado del perfil: se sube desde el nombre hasta la tarjeta que tiene «Información de
  // contacto» o hasta una sección, sin pasar a las tarjetas de abajo.
  let top = nameEl;
  const isHeader = (el) => /información de contacto|contact info|contactos|connections|seguidores|followers/i.test(el.innerText || "");
  while (top.parentElement && top.parentElement !== main && top.tagName !== "SECTION") {
    const up = top.parentElement;
    if (clean(up.innerText).length > 2500) break;
    top = up;
    if (isHeader(top) && clean(top.innerText).length > 60) break;
  }
  // Las líneas en el orden en que se ven, desde el nombre.
  const all = String(top.innerText || "").split(/\n|\s·\s|·/).map(clean).filter(Boolean);
  const start = Math.max(0, all.findIndex((l) => l === name || l.startsWith(name)));
  const lines = all.slice(start + 1).filter((l) => !NOISE.test(l) && l !== name);
  out.location = lines.find((l) => COUNTRY.test(l) && l.length < 90 && l.split(" ").length <= 9)
    || lines.find((l) => /^[^,|]{2,40},\s?[^,|]{2,40}(,\s?[^,|]{2,40})?$/.test(l)) || "";
  // Empresa actual: el elemento con logo al lado del nombre (no la universidad).
  const firm = top.querySelector('[aria-label^="Empresa actual"], [aria-label^="Current company"], [aria-label*="mpresa actual"], [aria-label*="urrent company"]');
  if (firm) out.company = clean(firm.getAttribute("aria-label").split(":").slice(1).join(":")).replace(/\.\s.*$/, "").replace(/\.\s*$/, "");
  if (!out.company) {
    const withLogo = [...top.querySelectorAll("a, button, li, div")]
      .filter((el) => el.querySelector("img, svg") && !el.contains(nameEl))
      .map((el) => clean(el.innerText))
      .filter((t) => t && t.length >= 2 && t.length <= 80 && !t.includes("\n") && t !== name && !NOISE.test(t) && !SCHOOL.test(t) && !COUNTRY.test(t));
    out.company = withLogo[0] || "";
  }
  const schools = new Set(lines.filter((l) => SCHOOL.test(l)));
  // Cargo: el titular, la primera línea con texto debajo del nombre.
  out.title = lines.find((l) => l !== out.location && l !== out.company && !schools.has(l) && l.split(" ").length >= 2) || "";
  if (out.title.length > 90 && out.title.includes(" | ")) out.title = out.title.split(" | ")[0];
  if (!out.company && out.title) {
    // «Gerente Comercial en Banco X» o «CTO at X»; «Licenciada en Gestión Hotelera» no es empresa.
    const m = out.title.match(/\s(at|en|@)\s(?!.*\s(?:at|en|@)\s)(.+)$/i);
    const before = m ? out.title.slice(0, m.index) : "";
    const ROLE = /\b(ceo|cfo|cto|cio|coo|cmo|ciso|chief|head|director|directora|gerente|jefe|jefa|vp|vicepresident|presidente|presidenta|manager|lead|líder|lider|coordinador|coordinadora|founder|fundador|fundadora|socio|socia|partner|analista|especialista|ejecutivo|ejecutiva|consultor|consultora|asesor|asesora|supervisor|supervisora|account|executive|officer|representative|representante|engineer|developer|desarrollador)/i;
    if (m && (m[1].toLowerCase() !== "en" || ROLE.test(before)) && !/^(licenciad|ingenier|estudiante|graduad|magíster|magister|bachiller|técnic|tecnic|abogad|contador|psicólog|médic)/i.test(before)) {
      out.company = clean(m[2]);
      out.title = clean(before);
    }
  }
  // La experiencia actual (primer puesto de «Experiencia»), si está cargada en la página: da el
  // cargo y la empresa reales aunque el titular sea otra cosa («Licenciada en …»).
  const heads = [...main.querySelectorAll("h2, h3, [id='experience'], span, div")]
    .filter((el) => /^(experiencia|experience)$/i.test(clean(el.innerText)) && !el.querySelector("h2, h3"));
  for (const head of heads) {
    let card = head;
    for (let i = 0; i < 8 && card.parentElement && clean(card.innerText).length < 40; i++) card = card.parentElement;
    while (card.parentElement && card.parentElement !== main && !/^(SECTION|LI)$/.test(card.tagName)
      && clean(card.parentElement.innerText).length < 4000 && !isHeader(card.parentElement)) card = card.parentElement;
    const xs = String(card.innerText || "").split("\n").map(clean).filter(Boolean)
      .filter((l) => !/^(experiencia|experience|mostrar|show|ver todo|see all)/i.test(l) && !NOISE.test(l));
    if (xs.length < 2) continue;
    let role = "";
    let company = "";
    if (DURATION.test(xs[1]) && xs[2]) {
      [company, role] = [xs[0], xs[2]]; // empresa con varios puestos: empresa, duración, puesto
    } else {
      role = xs[0];
      company = xs[1].split(/\s·\s|·/)[0];
    }
    company = clean(company.replace(/\b(jornada completa|tiempo completo|full-time|part-time|media jornada|autónomo|freelance|contrato|contract|prácticas|internship)\b.*$/i, ""));
    if (role && !DURATION.test(role) && role.length < 120) out.title = role;
    if (company && !DURATION.test(company) && company.length < 100 && !SCHOOL.test(company)) out.company = company;
    break;
  }
  return out;
}

// Sales Navigator: SOLO cuando la persona abre la extensión, y solo la ficha del posible cliente
// que tiene abierta (panel lateral o página del lead). Usa las marcas de la ficha (nombre,
// titular, empresa y ubicación); no recorre la lista de resultados.
function orkestaSalesNav() {
  const clean = (s) => String(s || "").replace(/\s+/g, " ").trim();
  const out = { name: "", title: "", company: "", location: "", profile: "", lead: "" };
  const names = [...document.querySelectorAll('[data-anonymize="person-name"]')].filter((el) => clean(el.innerText));
  // La ficha abierta no está dentro de la lista de resultados.
  const inList = (el) => !!el.closest("ol li, [data-x-search-result], .artdeco-list__item");
  const nameEl = names.find((el) => !inList(el)) || (location.pathname.startsWith("/sales/lead/") ? names[0] : null);
  if (!nameEl) return out;
  out.name = clean(nameEl.innerText);
  // El panel: el contenedor del nombre que también tiene el titular o la ubicación.
  let panel = nameEl;
  for (let i = 0; i < 12 && panel.parentElement; i++) {
    panel = panel.parentElement;
    if (panel.querySelector('[data-anonymize="headline"], [data-anonymize="location"]') && panel.querySelector('[data-anonymize="company-name"]')) break;
  }
  const pick = (attr) => clean(panel.querySelector(`[data-anonymize="${attr}"]`)?.innerText);
  out.title = pick("headline") || pick("job-title");
  out.company = pick("company-name");
  out.location = pick("location");
  if (!out.location) {
    const pin = [...panel.querySelectorAll("div, span")].map((e) => clean(e.innerText)).find((t) => /,/.test(t) && t.length < 60 && /colombia|méxico|mexico|chile|perú|peru|argentina|ecuador|panamá|costa rica|guatemala|uruguay|paraguay|bolivia|venezuela|dominicana|brasil|españa|estados unidos|united states|area|área/i.test(t));
    out.location = pin || "";
  }
  if (out.title.length > 70) out.title = out.title.split(/\s[|I•]\s/)[0];
  const profile = panel.querySelector('a[href*="linkedin.com/in/"]');
  if (profile) out.profile = profile.href;
  const lead = nameEl.closest('a[href*="/sales/lead/"]') || panel.querySelector('a[href*="/sales/lead/"]');
  if (lead) out.lead = lead.href;
  return out;
}

// Lista de personas en pantalla: búsqueda de Sales Navigator, búsqueda de personas de LinkedIn o
// una tabla de contactos (Prospeo, Apollo, un CRM…). Solo lo que ya está cargado y visible; no
// hace scroll ni pasa de página.
function orkestaList() {
  const clean = (s) => String(s || "").replace(/\s+/g, " ").trim();
  const shown = (el) => !el.closest('[hidden], [aria-hidden="true"]') && getComputedStyle(el).display !== "none";
  const EMAIL = /[A-Z0-9._%+'-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i;
  const AT = /\s(?:at|en|@)\s(?!.*\s(?:at|en|@)\s)(.+)$/i;
  const people = [];
  const seen = new Set();
  const push = (p) => {
    p = Object.fromEntries(Object.entries(p).map(([k, v]) => [k, clean(v)]));
    if (!p.name || p.name.length > 80 || /@|^\d+$/.test(p.name)) return;
    const key = `${p.name}|${p.company}`.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    people.push(p);
  };
  // La fila de una persona: el contenedor más grande que solo la tiene a ella.
  const rowOf = (el, count) => {
    let row = el;
    while (row.parentElement && row.parentElement !== document.body && count(row.parentElement) === 1) row = row.parentElement;
    return row;
  };

  // 1) Sales Navigator (marcas data-anonymize de cada resultado).
  if (/(^|\.)linkedin\.com$/.test(location.hostname) && location.pathname.startsWith("/sales/")) {
    const names = [...document.querySelectorAll('[data-anonymize="person-name"]')].filter((el) => clean(el.innerText));
    const count = (box) => box.querySelectorAll('[data-anonymize="person-name"]').length;
    for (const nameEl of names) {
      const row = rowOf(nameEl, count);
      const pick = (attr) => clean(row.querySelector(`[data-anonymize="${attr}"]`)?.innerText);
      let title = pick("title") || pick("job-title") || pick("headline");
      if (title.length > 90) title = title.split(/\s[|I•]\s/)[0];
      const lead = nameEl.closest('a[href*="/sales/lead/"]') || row.querySelector('a[href*="/sales/lead/"]');
      const profile = row.querySelector('a[href*="linkedin.com/in/"]');
      push({ name: nameEl.innerText, title, company: pick("company-name"), location: pick("location"),
        email: "", url: (profile && profile.href) || (lead && lead.href) || "" });
    }
    return { kind: "salesnav", people };
  }

  // 2) Búsqueda de personas de LinkedIn: cada resultado enlaza a un perfil /in/.
  if (/(^|\.)linkedin\.com$/.test(location.hostname) && location.pathname.startsWith("/search/")) {
    const NOISE = /^(•|·)|^\d+\s*(º|°)|\b(1er|2º|3er|1st|2nd|3rd)\b|conectar|connect|mensaje|message|seguir|follow|en común|in common|ver perfil|view .*profile|contactos|connections|seguidores|followers|^actual:|^current:|^anterior:|^past:|premium|verificad|verified|^status|^estado/i;
    const slug = (a) => (a.getAttribute("href").match(/\/in\/([^/?#]+)/) || [])[1] || "";
    const links = [...document.querySelectorAll('main a[href*="/in/"], a[href*="linkedin.com/in/"]')].filter((a) => slug(a));
    const count = (box) => new Set([...box.querySelectorAll('a[href*="/in/"]')].map(slug).filter(Boolean)).size;
    const done = new Set();
    for (const a of links) {
      const id = slug(a);
      if (done.has(id)) continue;
      const row = rowOf(a, count);
      const lines = String(row.innerText || "").split("\n").map(clean).filter(Boolean);
      const name = clean((a.querySelector('span[aria-hidden="true"]') || a).innerText).split("\n")[0]
        || lines[0] || "";
      if (!name || name.split(" ").length > 7) continue;
      done.add(id);
      const rest = lines.filter((l) => l !== name && !l.startsWith(name) && !NOISE.test(l));
      const headline = rest[0] || "";
      const at = headline.match(AT);
      push({ name: name.replace(/\s*[•·].*$/, ""), title: at ? headline.slice(0, at.index) : headline,
        company: at ? at[1] : "", location: rest[1] || "", email: "", url: `https://www.linkedin.com/in/${id}/` });
    }
    return { kind: "linkedin", people };
  }

  // 3) Tablas: encabezados por nombre (Name, Company, Title, Email, Location… en español o inglés).
  const ROLES = [
    ["first", /^(first ?name|nombres?|primer nombre)$/i],
    ["last", /^(last ?name|apellidos?)$/i],
    ["name", /^(name|full ?name|nombre completo|nombre|contact|contacto|person|persona|lead|prospect|prospecto)$/i],
    ["title", /^(title|job ?title|cargo|puesto|position|posición|role|rol|headline)$/i],
    ["company", /^(company|company name|empresa|organization|organización|account|cuenta|compañía)$/i],
    ["email", /(e-?mail|correo)/i],
    ["phone", /(phone|teléfono|telefono|mobile|móvil|celular)/i],
    ["location", /^(location|ubicación|ubicacion|country|país|pais|city|ciudad|region|región)$/i],
    ["url", /linkedin/i],
  ];
  const tables = [];
  document.querySelectorAll("table").forEach((t) => {
    const heads = [...t.querySelectorAll("thead th, thead td")];
    const headRow = heads.length ? heads : [...(t.querySelector("tr")?.querySelectorAll("th") || [])];
    if (!headRow.length) return;
    const rows = [...t.querySelectorAll("tbody tr, tr")].filter((r) => r.querySelector("td") && shown(r));
    tables.push({ heads: headRow.map((h) => clean(h.innerText)), rows: rows.map((r) => [...r.querySelectorAll("td")]) });
  });
  document.querySelectorAll('[role="grid"], [role="table"], [role="treegrid"]').forEach((g) => {
    const heads = [...g.querySelectorAll('[role="columnheader"]')];
    if (!heads.length) return;
    const rows = [...g.querySelectorAll('[role="row"]')].filter((r) => r.querySelector('[role="cell"], [role="gridcell"]') && shown(r));
    tables.push({ heads: heads.map((h) => clean(h.innerText)), rows: rows.map((r) => [...r.querySelectorAll('[role="cell"], [role="gridcell"]')]) });
  });
  for (const t of tables) {
    const map = {};
    t.heads.forEach((h, i) => {
      const role = ROLES.find(([key, re]) => !(key in map) && re.test(h));
      if (role) map[role[0]] = i;
    });
    if (!("name" in map) && !("first" in map)) continue;
    // Si la tabla tiene una columna de selección sin encabezado, las celdas van corridas.
    const shift = t.rows.length && t.rows[0].length === t.heads.length + 1 ? 1 : 0;
    for (const cells of t.rows) {
      const cell = (key) => (key in map && cells[map[key] + shift]) || null;
      const text = (key) => (cell(key) ? String(cell(key).innerText || "") : "");
      const nameLines = text("name").split("\n").map(clean).filter(Boolean);
      const name = nameLines[0] || clean(`${text("first")} ${text("last")}`);
      const emailCell = text("email");
      const email = (emailCell.match(EMAIL) || [])[0]
        || (cell("email")?.querySelector('a[href^="mailto:"]')?.getAttribute("href") || "").slice(7);
      const link = (cells.map((c) => c.querySelector('a[href*="linkedin.com/in/"]')).find(Boolean)) || null;
      const companyLines = text("company").split("\n").map(clean).filter(Boolean);
      push({ name, title: text("title").split("\n")[0] || nameLines[1] || "", company: companyLines[0] || "",
        location: text("location").split("\n")[0] || "", email: email || "", phone: text("phone").split("\n")[0] || "",
        url: (link && link.href) || "" });
    }
    if (people.length) return { kind: "table", people };
  }
  return { kind: "", people };
}
