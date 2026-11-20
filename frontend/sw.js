/* Servisni radnik za instaliranu aplikaciju (PWA).
 * - sučelje (HTML, JS, CSS, ikone) iz predmemorije – aplikacija se otvara odmah
 * - podaci (data/*.json): najprije mreža, a bez mreže zadnje preuzete cijene
 */
const VERSION = "bolani-__BUILD__";
const SHELL = ["./", "index.html", "app.js", "style.css", "vendor/chart.umd.js", "manifest.webmanifest",
  "icons/icon-192.png", "icons/icon-512.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.includes("/data/")) {
    // svježe cijene kad ima mreže, inače zadnje spremljene
    e.respondWith(fetch(e.request).then((r) => {
      const copy = r.clone();
      if (r.ok) caches.open(VERSION).then((c) => c.put(e.request, copy));
      return r;
    }).catch(() => caches.match(e.request)));
    return;
  }
  e.respondWith(caches.match(e.request).then((hit) => hit || fetch(e.request)));
});
