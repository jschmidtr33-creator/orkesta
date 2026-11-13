// Se ejecuta SOLO cuando la persona hace clic (botón de la extensión o «Guardar en ORKESTA»):
// lee lo que seleccionó, el nombre del sitio y, en Gmail, el remitente del correo abierto.
// Nunca recorre listas ni lee otras pestañas. En LinkedIn no se ejecuta.
function orkestaCapture() {
  const meta = (name) =>
    document.querySelector(`meta[property="${name}"], meta[name="${name}"]`)?.getAttribute("content") || "";
  const out = {
    selection: String(window.getSelection ? window.getSelection() : "").trim().slice(0, 2000),
    title: document.title.slice(0, 300),
    site: meta("og:site_name").slice(0, 200),
    host: location.hostname,
    url: location.href.slice(0, 600),
    sender: null,
  };
  if (location.hostname === "mail.google.com") {
    const senders = document.querySelectorAll("h3 span[email], span.gD[email]");
    const last = senders[senders.length - 1];
    if (last) out.sender = { name: last.getAttribute("name") || last.textContent || "", email: last.getAttribute("email") || "" };
  }
  return out;
}
