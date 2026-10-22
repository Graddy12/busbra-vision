"use strict";
// Compatibility worker: move old installed clients to the neutral workspace.
// Do NOT delete caches here: the new worker migrates the verified model files.
self.addEventListener("install", event => event.waitUntil(self.skipWaiting()));
self.addEventListener("activate", event => {
  event.waitUntil((async () => {
    await self.clients.claim();
    for (const client of await self.clients.matchAll({ type: "window" })) {
      if (new URL(client.url).pathname.startsWith("/offline/")) {
        await client.navigate(new URL("/workspace/", self.location.origin).href);
      }
    }
  })());
});
self.addEventListener("fetch", event => {
  const url = new URL(event.request.url);
  if (event.request.mode === "navigate" && url.origin === self.location.origin
      && url.pathname.startsWith("/offline/")) {
    event.respondWith(Response.redirect(new URL("/workspace/", self.location.origin).href, 302));
  }
});
