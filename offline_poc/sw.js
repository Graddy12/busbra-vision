"use strict";
const CACHE = "busbra-offline-poc-v12";
const PREVIOUS_CACHE = "busbra-offline-poc-v11";
const STATIC = ["./", "./index.html", "./styles.css", "./app.js", "./preprocess.js", "./inference-worker.js",
  "./manifest.webmanifest", "./icon.svg", "./assets-manifest.json", "./vendor/ort.wasm.min.js",
  "./vendor/ort-wasm-simd-threaded.mjs", "./vendor/ort-wasm-simd-threaded.wasm"];
const ALLOWED = new Set(STATIC.map(value => new URL(value, self.registration.scope).pathname));
ALLOWED.add(new URL("./models/classification.onnx", self.registration.scope).pathname);
ALLOWED.add(new URL("./models/segmentation.onnx", self.registration.scope).pathname);
self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(STATIC)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", event => {
  event.waitUntil((async () => {
    const current = await caches.open(CACHE);
    const previous = await caches.open(PREVIOUS_CACHE);
    for (const path of ["./__offline_ready__", "./models/classification.onnx", "./models/segmentation.onnx"]) {
      const target = new URL(path, self.registration.scope);
      let response = await previous.match(target);
      if (!response && target.pathname.startsWith("/workspace/")) {
        const legacy = new URL("/offline/" + path.slice(2), self.location.origin);
        response = await previous.match(legacy);
      }
      if (response) await current.put(target, response);
    }
    await Promise.all((await caches.keys())
      .filter(name => name.startsWith("busbra-offline-poc-") && name !== CACHE)
      .map(name => caches.delete(name)));
    await self.clients.claim();
  })());
});
self.addEventListener("fetch", event => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin || !ALLOWED.has(url.pathname) || url.search) return;
  event.respondWith((async () => {
    // Explicit reload is used to prepare/verify all assets while actually online.
    if (event.request.cache === "reload") return fetch(event.request);
    const cached = await (await caches.open(CACHE)).match(event.request);
    if (cached) return cached;
    return fetch(event.request);
  })());
});
