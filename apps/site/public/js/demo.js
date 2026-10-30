// Formulario «Solicitar demo»: envía a la API de ORKESTA (mismo origen).
(function () {
  "use strict";
  var form = document.getElementById("demo-form");
  if (!form) return;
  var status = document.getElementById("demo-status");
  var button = form.querySelector("button[type=submit]");

  function show(message, ok) {
    status.textContent = message;
    status.className = "text-[13.5px] sm:col-span-2 " + (ok ? "text-ok" : "text-bad");
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var data = new FormData(form);
    if (!data.get("name") || !data.get("email") || !data.get("company")) {
      show("Escribe tu nombre, tu correo y tu empresa.", false);
      return;
    }
    if (!data.get("consent")) {
      show("Necesitamos tu autorización para contactarte.", false);
      return;
    }
    button.disabled = true;
    button.textContent = "Enviando…";
    fetch("/api/public/demo-requests", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: data.get("name"),
        email: data.get("email"),
        company: data.get("company"),
        job_title: data.get("job_title") || "",
        country: data.get("country") || "",
        message: data.get("message") || "",
        consent: true,
      }),
    })
      .then(function (response) {
        return response.json().then(function (body) {
          return { ok: response.ok, body: body };
        });
      })
      .then(function (result) {
        if (result.ok) {
          form.reset();
          show("¡Gracias! Te contactaremos pronto para agendar la demo.", true);
        } else {
          show(typeof result.body.detail === "string" ? result.body.detail : "Revisa los datos del formulario.", false);
        }
      })
      .catch(function () {
        show("No pudimos enviar tu solicitud. Inténtalo de nuevo.", false);
      })
      .finally(function () {
        button.disabled = false;
        button.textContent = "Solicitar demo";
      });
  });
})();
