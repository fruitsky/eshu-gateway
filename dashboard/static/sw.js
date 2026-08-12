// Eshu Gateway — service worker removed.
// This stub exists only to unregister any previously-installed service worker
// and clear its caches. It self-destructs on activation. The dashboard no
// longer uses a service worker (cache-first caching caused stale app.js and
// stale /api GET responses, masking deploys and API state changes).
self.addEventListener('install', function() { self.skipWaiting(); });

self.addEventListener('activate', function(e) {
  e.waitUntil(
    caches.keys().then(function(keys) {
      return Promise.all(keys.map(function(k) { return caches.delete(k); }));
    }).then(function() {
      return self.registration.unregister();
    })
  );
});
