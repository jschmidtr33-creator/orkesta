/* ORKESTA · notificaciones push (Web Push estándar, sin servicios de terceros). */
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (_) {
    data = { body: event.data ? event.data.text() : "" };
  }
  const link = typeof data.link === "string" && data.link.startsWith("/") ? data.link : "/app/";
  event.waitUntil(
    self.registration.showNotification(data.title || "ORKESTA", {
      body: data.body || "",
      tag: data.tag || undefined,
      icon: "/app/apple-touch-icon.png",
      badge: "/app/apple-touch-icon.png",
      data: { link },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const link = (event.notification.data && event.notification.data.link) || "/app/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((wins) => {
      for (const win of wins) {
        if (new URL(win.url).pathname.startsWith("/app")) {
          win.focus();
          return win.navigate ? win.navigate(link) : undefined;
        }
      }
      return self.clients.openWindow(link);
    }),
  );
});
