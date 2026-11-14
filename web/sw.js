// Lets browsers install Archiver as an app (and offer it in the share sheet on Android).
// Nothing is cached: the library always comes from your server.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
