self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("push", (event) => {
  let payload = { title: "قیمت دلار", body: "" };

  try {
    payload = event.data.json();
  } catch (e) {
    if (event.data) payload.body = event.data.text();
  }

  event.waitUntil(
    self.registration.showNotification(
      payload.title || "قیمت دلار",
      {
        body: payload.body || "",
        icon: "/icon-192.png",
        badge: "/icon-192.png",
        vibrate: [120, 60, 120],
      }
    )
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();

  event.waitUntil(
    clients.matchAll({ type: "window" }).then((clientList) => {
      if (clientList.length > 0) {
        return clientList[0].focus();
      }

      return clients.openWindow("/");
    })
  );
});