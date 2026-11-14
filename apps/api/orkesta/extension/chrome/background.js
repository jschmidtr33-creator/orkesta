// Tocar el ícono de la extensión (o «Guardar en ORKESTA» en el menú del clic derecho) abre el
// panel flotante en la pestaña que estás viendo. En páginas donde el navegador no deja abrirlo
// (chrome://, la tienda de Chrome) se abre la extensión en una ventana pequeña.
async function openPanel(tab) {
  if (!tab || !tab.id) return;
  try {
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["capture.js", "panel.js"] });
  } catch (e) {
    chrome.windows.create({ url: "popup.html?ventana=1", type: "popup", width: 400, height: 640 });
  }
}

chrome.action.onClicked.addListener(openPanel);

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({ id: "orkesta-guardar", title: "Guardar en ORKESTA", contexts: ["selection", "page"] });
});

chrome.contextMenus.onClicked.addListener((info, tab) => openPanel(tab));

// Solo el panel que abrió la propia extensión puede mostrar la ventana de ORKESTA: cada panel
// registra aquí su marca y la ventana la confirma antes de mostrar nada (una página no puede
// incrustarla por su cuenta para engañar a la persona).
chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (!msg || typeof msg.nonce !== "string" || msg.nonce.length < 16) return;
  (async () => {
    const { panels = {} } = await chrome.storage.session.get("panels");
    if (msg.type === "panel-open" && sender.tab) {
      panels[msg.nonce] = Date.now();
      for (const [k, at] of Object.entries(panels)) if (Date.now() - at > 7 * 864e5) delete panels[k];
      await chrome.storage.session.set({ panels });
      reply({ ok: true });
    } else if (msg.type === "panel-check" && sender.url && sender.url.startsWith(chrome.runtime.getURL(""))) {
      reply({ ok: !!panels[msg.nonce] });
    } else {
      reply({ ok: false });
    }
  })();
  return true;
});
