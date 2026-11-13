// Solo en https://orkestaapp.com/app/conectar-extension: recibe de la página de ORKESTA la clave
// de la persona que acaba de iniciar sesión y la guarda en este navegador.
const ORIGIN = "https://orkestaapp.com";
function hello() {
  window.postMessage({ type: "orkesta-extension-ready" }, ORIGIN);
}
window.addEventListener("message", async (event) => {
  if (event.source !== window || event.origin !== ORIGIN) return;
  const data = event.data || {};
  if (data.type === "orkesta-extension-ping") hello();
  if (data.type === "orkesta-extension-token" && typeof data.token === "string" && data.token.startsWith("ork_")) {
    await chrome.storage.local.set({ token: data.token });
    window.postMessage({ type: "orkesta-extension-saved" }, ORIGIN);
  }
});
hello();
