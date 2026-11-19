// Precios: toma los valores del panel de administración (mismo origen) y alterna mensual / anual.
(function () {
  "use strict";
  var section = document.getElementById("precios");
  if (!section) return;
  var billing = "year";
  var data = null;
  var fmt = function (n) { return Number(n || 0).toLocaleString("es-CO"); };

  function render() {
    section.querySelectorAll("[data-billing]").forEach(function (b) {
      b.setAttribute("aria-pressed", String(b.getAttribute("data-billing") === billing));
    });
    if (!data) return;
    data.plans.forEach(function (p) {
      var card = section.querySelector('[data-plan="' + p.code + '"]');
      if (!card) return;
      var set = function (field, value) {
        var el = card.querySelector('[data-field="' + field + '"]');
        if (el) el.textContent = value;
      };
      set("price", fmt(billing === "year" ? p.price_year : p.price_month));
      set("billing-note", billing === "year"
        ? "Pago anual · US$" + fmt(p.price_month) + " mes a mes"
        : "Mes a mes · US$" + fmt(p.price_year) + " con pago anual");
      set("verifications_per_user", fmt(p.verifications_per_user));
      set("min_users", fmt(p.min_users));
      var each = billing === "year" ? p.price_year : p.price_month;
      set("company-from", fmt(each * Math.max(1, p.min_users || 1)));
    });
    var put = function (field, value) {
      var el = section.querySelector('[data-field="' + field + '"]');
      if (el) el.textContent = value;
    };
    put("demo-days", data.demo.days);
    put("package-verifications", fmt(data.package.verifications));
    put("package-price", fmt(data.package.price));
  }

  section.querySelectorAll("[data-billing]").forEach(function (b) {
    b.addEventListener("click", function () {
      billing = b.getAttribute("data-billing");
      if (!data) {
        // Sin datos del panel: cambia entre los precios de referencia escritos en la página.
        section.querySelectorAll("[data-plan]").forEach(function (card) {
          var price = card.querySelector('[data-field="price"]');
          var note = card.querySelector('[data-field="billing-note"]');
          var m = (note.textContent.match(/US\$(\d+)/) || [])[1];
          if (m) {
            var other = price.textContent;
            price.textContent = m;
            var from = card.querySelector('[data-field="company-from"]');
            var min = card.querySelector('[data-field="min_users"]');
            if (from && min) from.textContent = fmt(Number(m) * Number(min.textContent));
            note.textContent = billing === "year" ? "Pago anual · US$" + other + " mes a mes" : "Mes a mes · US$" + other + " con pago anual";
          }
        });
      }
      render();
    });
  });

  fetch("/api/public/pricing")
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (json) { if (json && json.plans) { data = json; render(); } })
    .catch(function () {});
  render();
})();
