// Tema antes de pintar, para que la versión oscura no parpadee en claro. Va en un archivo propio
// (no incrustado) porque la política de seguridad de la app no permite scripts en línea.
try {
  var t = localStorage.getItem("ork-theme");
  var d = t === "dark" || (t === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  if (d && location.pathname.indexOf("/app/p/") !== 0) document.documentElement.classList.add("dark");
} catch (e) {}
