"use strict";

self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (_error) {
    payload = { body: event.data ? event.data.text() : "Nova disponibilidade encontrada." };
  }

  event.waitUntil(
    self.registration.showNotification(payload.title || "EFVM Monitor", {
      body: payload.body || "Nova disponibilidade encontrada.",
      icon: payload.icon || "/static/icons/icon-192.png",
      badge: payload.badge || "/static/icons/badge-96.png",
      tag: payload.tag || "efvm-disponibilidade",
      renotify: false,
      data: { url: payload.url || "/" },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetUrl = event.notification.data?.url || "/";
  event.waitUntil(
    (async () => {
      const target = new URL(targetUrl, self.location.origin);
      if (target.origin !== self.location.origin) {
        return self.clients.openWindow ? self.clients.openWindow(target.href) : undefined;
      }
      const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const client of windows) {
        if ("navigate" in client) await client.navigate(target.href);
        if ("focus" in client) return await client.focus();
      }
      return self.clients.openWindow ? self.clients.openWindow(target.href) : undefined;
    })(),
  );
});
