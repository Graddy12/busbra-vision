"use strict";
const CACHE = "busbra-offline-poc-v1";
const STATIC = ["./", "./index.html", "./styles.css", "./app.js", "./preprocess.js", "./inference-worker.js",
  "./manifest.webmanifest", "./icon.svg", "./assets-manifest.json", "./vendor/ort.wasm.min.js",
  "./vendor/ort-wasm-simd-threaded.mjs", "./vendor/ort-wasm-simd-threaded.wasm"];
const ALLOWED = new Set(STATIC.map(value => new URL(value, self.registration.scope).pathname));
ALLOWED.add(new URL("./models/classification.onnx", self.registration.scope).pathname);
ALLOWED.add(new URL("./models/segmentation.onnx", self.registration.scope).pathname);
self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(STATIC)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", event => { event.waitUntil(self.clients.claim()); });
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
