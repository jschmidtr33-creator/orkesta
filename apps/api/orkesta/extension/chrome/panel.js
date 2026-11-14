// El panel flotante de ORKESTA: una pestaña pequeña en el borde derecho y una ventana que se puede
// arrastrar desde su barra superior, minimizar a la pestaña o cerrar. Recuerda dónde la dejaste.
// Lee la página (con capture.js) solo cuando la ventana se abre o tocas «Leer de nuevo», y le pasa
// lo leído a la ventana de la extensión. No hace nada más en la página.
(() => {
  if (window.__orkestaPanel) {
    window.__orkestaPanel.open();
    return;
  }
  const EXT = chrome.runtime.getURL("");
  const nonce = [...crypto.getRandomValues(new Uint8Array(16))].map((b) => b.toString(16).padStart(2, "0")).join("");
  const registered = chrome.runtime.sendMessage({ type: "panel-open", nonce }).catch(() => null);
  const host = document.createElement("div");
  host.style.cssText = "all:initial;position:fixed;z-index:2147483646;top:0;left:0;width:0;height:0;";
  const root = host.attachShadow({ mode: "closed" });
  root.innerHTML = `
    <style>
      :host { all: initial; }
      .tab { position: fixed; right: 0; top: 38%; width: 34px; padding: 10px 0; display: flex; flex-direction: column;
        align-items: center; gap: 8px; background: #0b1020; border-radius: 10px 0 0 10px; cursor: pointer;
        box-shadow: 0 6px 18px rgba(15,23,42,.28); font: 700 11px/1 system-ui, sans-serif; color: #fff; letter-spacing: .08em; }
      .tab img { width: 20px; height: 20px; }
      .tab span { writing-mode: vertical-rl; transform: rotate(180deg); }
      .tab:hover { width: 38px; }
      .win { position: fixed; width: 380px; height: min(620px, calc(100vh - 24px)); display: flex; flex-direction: column;
        background: #f7f8fc; border-radius: 14px; overflow: hidden; box-shadow: 0 18px 50px rgba(15,23,42,.35);
        border: 1px solid #1e293b22; }
      .bar { display: flex; align-items: center; gap: 8px; padding: 9px 10px 9px 14px; background: #0b1020; color: #fff;
        cursor: grab; user-select: none; font: 700 13px system-ui, sans-serif; }
      .bar.drag { cursor: grabbing; }
      .bar img { width: 20px; height: 20px; }
      .bar .sp { flex: 1; }
      .bar button { all: unset; width: 26px; height: 26px; display: grid; place-items: center; border-radius: 7px;
        cursor: pointer; color: #c7cde0; font: 600 16px/1 system-ui, sans-serif; }
      .bar button:hover { background: #ffffff1f; color: #fff; }
      iframe { flex: 1; border: 0; width: 100%; background: #f7f8fc; }
      [hidden] { display: none !important; }
    </style>
    <div class="tab" title="Abrir ORKESTA"><img alt="" /><span>ORKESTA</span></div>
    <div class="win" hidden>
      <div class="bar" title="Arrastra para mover">
        <img alt="" /><span>ORKESTA</span><span class="sp"></span>
        <button class="min" title="Minimizar">–</button>
        <button class="close" title="Cerrar">×</button>
      </div>
    </div>`;
  const tab = root.querySelector(".tab");
  const win = root.querySelector(".win");
  const bar = root.querySelector(".bar");
  root.querySelectorAll("img").forEach((img) => (img.src = chrome.runtime.getURL("icons/icon32.png")));
  let frame = null;

  const place = (pos) => {
    const w = 380;
    const h = Math.min(620, window.innerHeight - 24);
    const x = Math.min(Math.max(8, pos.x), window.innerWidth - w - 8);
    const y = Math.min(Math.max(8, pos.y), Math.max(8, window.innerHeight - h - 8));
    win.style.left = `${x}px`;
    win.style.top = `${y}px`;
    return { x, y };
  };
  let pos = { x: window.innerWidth - 400, y: 70 };
  chrome.storage.local.get("panelPos").then(({ panelPos }) => {
    if (panelPos) pos = panelPos;
    place(pos);
  });

  // Arrastrar desde la barra.
  bar.addEventListener("pointerdown", (e) => {
    if (e.target.closest("button")) return;
    const start = { x: e.clientX, y: e.clientY, left: win.offsetLeft, top: win.offsetTop };
    bar.classList.add("drag");
    bar.setPointerCapture(e.pointerId);
    if (frame) frame.style.pointerEvents = "none";
    const move = (ev) => {
      pos = place({ x: start.left + ev.clientX - start.x, y: start.top + ev.clientY - start.y });
    };
    const up = () => {
      bar.classList.remove("drag");
      bar.removeEventListener("pointermove", move);
      bar.removeEventListener("pointerup", up);
      if (frame) frame.style.pointerEvents = "";
      chrome.storage.local.set({ panelPos: pos });
    };
    bar.addEventListener("pointermove", move);
    bar.addEventListener("pointerup", up);
  });
  window.addEventListener("resize", () => win.hidden || place(pos));

  // Lo que hay en pantalla, para la ventana de la extensión.
  const read = () => {
    const out = { url: location.href.slice(0, 600), host: location.hostname, list: { kind: "", people: [] } };
    try { out.list = orkestaList(); } catch (e) {}
    const li = /(^|\.)linkedin\.com$/.test(location.hostname);
    try {
      if (li && location.pathname.startsWith("/in/")) out.linkedin = orkestaLinkedIn();
      else if (li && location.pathname.startsWith("/sales/")) out.salesnav = orkestaSalesNav();
      else if (!li) out.page = orkestaCapture();
    } catch (e) {}
    return out;
  };
  const send = () => {
    if (frame && frame.contentWindow) frame.contentWindow.postMessage({ type: "orkesta-page", nonce, page: read() }, EXT.replace(/\/$/, ""));
  };
  window.addEventListener("message", (e) => {
    if (!frame || e.source !== frame.contentWindow) return;
    const d = e.data || {};
    if (d.nonce !== nonce) return;
    if (d.type === "orkesta-read") send();
    if (d.type === "orkesta-minimize") minimize();
  });

  async function open() {
    if (!host.isConnected) document.documentElement.appendChild(host);
    tab.hidden = true;
    win.hidden = false;
    place(pos);
    if (!frame) {
      await registered;
      if (frame) return send();
      frame = document.createElement("iframe");
      frame.allow = "clipboard-write";
      frame.src = chrome.runtime.getURL(`popup.html?panel=1#${nonce}`);
      win.appendChild(frame);
    } else {
      send();
    }
  }
  function minimize() {
    win.hidden = true;
    tab.hidden = false;
  }
  function close() {
    host.remove();
  }
  tab.addEventListener("click", open);
  root.querySelector(".min").addEventListener("click", minimize);
  root.querySelector(".close").addEventListener("click", close);

  // LinkedIn cambia de perfil o de búsqueda sin recargar: se vuelve a leer al cambiar la dirección.
  let last = location.href;
  setInterval(() => {
    if (location.href === last) return;
    last = location.href;
    if (frame && !win.hidden) setTimeout(send, 1500);
  }, 800);

  window.__orkestaPanel = { open, minimize, close };
  // En LinkedIn aparece solo la pestaña; en otras páginas se abre porque tocaste el ícono.
  if (window.__orkestaAuto) {
    document.documentElement.appendChild(host);
    win.hidden = true;
  } else {
    open();
  }
})();
