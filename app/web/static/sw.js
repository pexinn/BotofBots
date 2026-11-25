// 100% Network Pass-Through Service Worker (sem cache agressivo para API/HTML)
self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;
  const url = event.request.url || '';
  if (url.includes('/api/')) return;
  event.respondWith(fetch(event.request));
});
