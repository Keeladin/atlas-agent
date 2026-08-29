/// <reference lib="webworker" />
import { createHandlerBoundToURL, precacheAndRoute } from 'workbox-precaching'
import { registerRoute } from 'workbox-routing'

declare const self: ServiceWorkerGlobalScope

precacheAndRoute(self.__WB_MANIFEST)
const shell = createHandlerBoundToURL('/index.html')
registerRoute(
  ({ request, url }) => request.mode === 'navigate' && !url.pathname.startsWith('/api/'),
  shell,
)
self.skipWaiting()
self.addEventListener('activate', () => { void self.clients.claim() })
