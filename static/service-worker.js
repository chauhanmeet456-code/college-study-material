'use strict';

const STATIC_CACHE = 'studyhub-static-v1';
const PRECACHE_URLS = [
  '/static/offline.html',
  '/static/css/cookie-consent.css',
  '/static/js/studyhub.js',
  '/static/js/pwa.js',
  '/static/favicons/favicon.svg',
  '/static/images/og-studyhub.png'
];

function isCacheableStatic(url) {
  return url.pathname.startsWith('/static/')
    && !url.pathname.includes('/uploads/')
    && /\.(css|js|png|svg|webp|woff2?)$/i.test(url.pathname);
}

function isPublicNavigation(url) {
  const path = url.pathname;
  return [
    '/',
    '/subjects',
    '/notes',
    '/assignments',
    '/books',
    '/practicals',
    '/question-papers',
    '/important-questions',
    '/syllabus',
    '/search',
    '/about',
    '/contact',
    '/cookie-policy',
    '/privacy-policy',
    '/terms-of-service'
  ].includes(path)
    || /^\/subject\/\d+$/.test(path)
    || /^\/material\/\d+$/.test(path);
}

self.addEventListener('install', event => {
  event.waitUntil((async () => {
    const cache = await caches.open(STATIC_CACHE);
    await cache.addAll(PRECACHE_URLS);
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    const cacheNames = await caches.keys();
    await Promise.all(cacheNames
      .filter(name => name.startsWith('studyhub-') && name !== STATIC_CACHE)
      .map(name => caches.delete(name)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (isCacheableStatic(url)) {
    event.respondWith((async () => {
      const cache = await caches.open(STATIC_CACHE);
      const cacheKey = url.origin + url.pathname;
      const cached = await cache.match(cacheKey);
      const update = fetch(request).then(response => {
        const cacheControl = response.headers.get('Cache-Control') || '';
        if (
          response.ok
          && response.type === 'basic'
          && !/\b(private|no-store)\b/i.test(cacheControl)
        ) {
          cache.put(cacheKey, response.clone());
        } else if (response.status === 404) {
          cache.delete(cacheKey);
        }
        return response;
      }).catch(() => cached);

      return cached || update;
    })());
    return;
  }

  if (request.mode === 'navigate' && isPublicNavigation(url)) {
    event.respondWith(fetch(request).catch(async () => {
      const offlinePage = await caches.match('/static/offline.html');
      return offlinePage || new Response('You are offline.', {
        status: 503,
        headers: {
          'Content-Type': 'text/plain; charset=utf-8',
          'Cache-Control': 'no-store'
        }
      });
    }));
  }
});
