const CACHE_NAME = "marko-capture-v2";
const OFFLINE_ASSETS = [
  "/capture",
  "/capture.webmanifest",
  "/static/css/capture.css",
  "/static/js/capture.js",
  "/webAssets/qq2.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(OFFLINE_ASSETS)),
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key !== CACHE_NAME)
          .map((key) => caches.delete(key)),
      ),
    ),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") {
    return;
  }

  const url = new URL(request.url);
  if (url.pathname.startsWith("/api/")) {
    return;
  }
  const isCapturePage = url.pathname === "/capture";
  const isStaticCaptureAsset =
    url.pathname === "/capture.webmanifest" ||
    url.pathname === "/capture-sw.js" ||
    url.pathname === "/static/css/capture.css" ||
    url.pathname === "/static/js/capture.js" ||
    url.pathname === "/webAssets/qq2.png";

  if (isCapturePage) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put("/capture", copy));
          return response;
        })
        .catch(() => caches.match("/capture")),
    );
    return;
  }

  if (isStaticCaptureAsset) {
    event.respondWith(
      caches.match(request).then((cached) => {
        if (cached) {
          return cached;
        }
        return fetch(request).then((response) => {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
          return response;
        });
      }),
    );
  }
});
