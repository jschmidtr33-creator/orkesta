// Movimiento de la landing: apariciones al hacer scroll, luz que sigue al cursor, burbujas de
// eventos, carrusel de pantallas reales y línea de proceso. Sin dependencias.
(function () {
  "use strict";
  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Apariciones al hacer scroll
  var revealed = document.querySelectorAll("[data-reveal]");
  if ("IntersectionObserver" in window && !reduced) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) {
          e.target.classList.add("in");
          io.unobserve(e.target);
        }
      });
    }, { threshold: 0.12, rootMargin: "0px 0px -40px 0px" });
    revealed.forEach(function (el) { io.observe(el); });
  } else {
    revealed.forEach(function (el) { el.classList.add("in"); });
  }

  // Luz que sigue al cursor en la portada
  var hero = document.getElementById("hero");
  var spot = hero && hero.querySelector(".spotlight");
  if (hero && spot && !reduced) {
    hero.addEventListener("pointermove", function (e) {
      var r = hero.getBoundingClientRect();
      spot.style.setProperty("--mx", (e.clientX - r.left) + "px");
      spot.style.setProperty("--my", (e.clientY - r.top) + "px");
    });
  }

  // Luz en las tarjetas de módulos
  document.querySelectorAll(".card-light").forEach(function (card) {
    card.addEventListener("pointermove", function (e) {
      var r = card.getBoundingClientRect();
      card.style.setProperty("--cx", (e.clientX - r.left) + "px");
      card.style.setProperty("--cy", (e.clientY - r.top) + "px");
    });
  });

  // Burbujas de eventos sobre el video, en secuencia
  var toasts = document.querySelectorAll("[data-toast]");
  if (toasts.length) {
    var t = 0;
    var cycle = function () {
      toasts.forEach(function (el, i) { el.classList.toggle("show", i <= t); });
      t = (t + 1) % (toasts.length + 1);
      if (t === 0) setTimeout(function () { toasts.forEach(function (el) { el.classList.remove("show"); }); }, 2600);
    };
    setTimeout(cycle, 1200);
    setInterval(cycle, 3200);
  }

  // Carrusel del recorrido
  var tour = document.getElementById("tour");
  if (tour) {
    var tabs = tour.querySelectorAll(".tour-tab");
    var slides = tour.querySelectorAll("[data-slide]");
    var captions = tour.querySelectorAll("[data-caption]");
    var url = document.getElementById("tour-url");
    var pauseBtn = document.getElementById("tour-pause");
    var DURATION = 6000;
    var current = 0;
    var timer = null;
    var paused = reduced;
    var visible = false;

    var show = function (i, focus) {
      current = (i + slides.length) % slides.length;
      slides.forEach(function (s, n) {
        s.classList.toggle("active", n === current);
        s.setAttribute("aria-hidden", n === current ? "false" : "true");
      });
      captions.forEach(function (c, n) { c.classList.toggle("hidden", n !== current); });
      tabs.forEach(function (tab, n) {
        tab.setAttribute("aria-selected", n === current ? "true" : "false");
        var bar = tab.querySelector(".progress");
        bar.classList.remove("run");
        void bar.offsetWidth; // reinicia la animación
        if (n === current && !paused) {
          bar.style.setProperty("--dur", DURATION + "ms");
          bar.classList.add("run");
        }
      });
      if (url) url.textContent = "ORKESTA · " + (tabs[current].getAttribute("data-tag") || "");
      var active = tabs[current];
      if (active && active.parentElement && window.innerWidth < 1024) {
        active.parentElement.parentElement.scrollTo({ left: active.parentElement.offsetLeft - 16, behavior: reduced ? "auto" : "smooth" });
      }
      if (focus) active.focus();
      schedule();
    };
    var schedule = function () {
      clearTimeout(timer);
      if (!paused && visible) timer = setTimeout(function () { show(current + 1); }, DURATION);
    };
    tabs.forEach(function (tab, n) {
      tab.addEventListener("click", function () { show(n); });
      tab.addEventListener("keydown", function (e) {
        if (e.key === "ArrowRight" || e.key === "ArrowDown") { e.preventDefault(); show(current + 1, true); }
        if (e.key === "ArrowLeft" || e.key === "ArrowUp") { e.preventDefault(); show(current - 1, true); }
      });
    });
    document.getElementById("tour-prev").addEventListener("click", function () { show(current - 1); });
    document.getElementById("tour-next").addEventListener("click", function () { show(current + 1); });
    pauseBtn.addEventListener("click", function () {
      paused = !paused;
      pauseBtn.textContent = paused ? "Reproducir" : "Pausar";
      pauseBtn.setAttribute("aria-label", paused ? "Reproducir el recorrido" : "Pausar el recorrido");
      show(current);
    });
    if (paused) pauseBtn.textContent = "Reproducir";
    // Deslizar en el celular
    var startX = null;
    var stage = tour.querySelector(".aspect-\\[16\\/10\\]");
    if (stage) {
      stage.addEventListener("touchstart", function (e) { startX = e.touches[0].clientX; }, { passive: true });
      stage.addEventListener("touchend", function (e) {
        if (startX === null) return;
        var dx = e.changedTouches[0].clientX - startX;
        if (Math.abs(dx) > 40) show(current + (dx < 0 ? 1 : -1));
        startX = null;
      });
    }
    // Solo avanza cuando está a la vista
    if ("IntersectionObserver" in window) {
      new IntersectionObserver(function (entries) {
        visible = entries[0].isIntersecting;
        show(current);
      }, { threshold: 0.3 }).observe(tour);
    } else {
      visible = true;
      show(0);
    }
  }

  // Línea del proceso que se llena con el scroll
  var flow = document.getElementById("flow");
  var fill = flow && flow.querySelector(".track-fill");
  if (flow && fill) {
    var update = function () {
      var r = flow.getBoundingClientRect();
      var p = (window.innerHeight * 0.65 - r.top) / r.height;
      fill.style.setProperty("--p", String(Math.max(0, Math.min(1, p))));
    };
    window.addEventListener("scroll", update, { passive: true });
    update();
  }
})();
