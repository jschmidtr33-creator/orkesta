importScripts("capture.js");

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({ id: "orkesta-guardar", title: "Guardar en ORKESTA", contexts: ["selection", "page"] });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (!tab || !tab.id) return;
  let page = { selection: info.selectionText || "", url: tab.url || "", title: tab.title || "", host: "", site: "", sender: null };
  const isLinkedIn = /(^|\.)linkedin\.com$/i.test(new URL(tab.url || "https://x").hostname);
  if (!isLinkedIn) {
    try {
      const [res] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: orkestaCapture });
      if (res && res.result) page = { ...res.result, selection: info.selectionText || res.result.selection };
    } catch (e) {
      // Páginas del navegador o protegidas: se usa solo lo seleccionado.
    }
  }
  await chrome.storage.session.set({ pending: { ...page, at: Date.now() } });
  try {
    await chrome.action.openPopup();
  } catch (e) {
    chrome.windows.create({ url: "popup.html?ventana=1", type: "popup", width: 420, height: 680 });
  }
});
