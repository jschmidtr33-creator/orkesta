// Progreso en vivo de la búsqueda web, sin recargar la página (mejora progresiva: sin
// JavaScript, el botón «Ver resultados» hace lo mismo).
(function () {
  "use strict";
  var panel = document.getElementById("live-panel");
  if (!panel || panel.dataset.running !== "1") return;
  var runId = panel.dataset.run;
  var detail = document.getElementById("live-detail");
  var title = document.getElementById("live-title");
  var progress = document.getElementById("live-progress");
  var refresh = document.getElementById("live-refresh");
  var noResults = !document.getElementById("a-lista");
  var fmt = new Intl.NumberFormat("es");
  var startFound = null;

  function tick() {
    fetch("/prospectos/busqueda/" + encodeURIComponent(runId), { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (s) {
        if (!s) return setTimeout(tick, 5000);
        if (startFound === null) startFound = s.people_found;
        detail.innerHTML = "";
        detail.append(fmt.format(s.done) + " de " + fmt.format(s.total) + " empresas revisadas · ");
        var strong = document.createElement("strong");
        strong.textContent = fmt.format(s.people_found);
        detail.append(strong, " decisores nuevos");
        if (progress) { progress.max = s.total; progress.value = s.done; }
        var fresh = s.people_found - startFound;
        if (fresh > 0 && noResults) return window.location.reload();
        if (fresh > 0) {
          refresh.textContent = "Ver " + fmt.format(s.people_found) + " nuevos";
          refresh.classList.remove("button-secondary");
        }
        if (s.running) return setTimeout(tick, 3000);
        title.textContent = "Búsqueda web terminada";
        panel.classList.add("done");
        if (progress) progress.remove();
        // Sin resultados en pantalla y con hallazgos: mostrarlos directamente.
        if (noResults && s.people_found > 0) window.location.reload();
      })
      .catch(function () { setTimeout(tick, 5000); });
  }
  setTimeout(tick, 2500);
})();
