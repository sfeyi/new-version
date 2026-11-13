// VERSION and ASSETS are generated together from the bytes deployed to Pages.
const VERSION = "__BUILD_ID__";
const ASSETS = __BUILD_ASSETS__;
const prefix = "portrait-guide:" + self.registration.scope + ":";
const cacheName = prefix + VERSION;
const root = self.registration.scope;
const indexURL = new URL("index.html", root).href;
const paths = new Map(ASSETS.map(item => [new URL(item.path, root).href, item]));
const total = ASSETS.reduce((n, item) => n + item.size, 0);

async function storeAsset(cache, entry) {
  const url = new URL(entry.path, root);
  const response = await fetch(url, {cache:"reload", integrity:"sha256-" + entry.integrity});
  if (!response.ok) throw new Error("无法下载 " + entry.path);
  await cache.put(url, response);
}

async function count(cache) {
  const urls = new Set((await cache.keys()).map(request => request.url));
  const bytes = ASSETS.reduce((n, item) => n + (urls.has(new URL(item.path, root).href) ? item.size : 0), 0);
  return {urls, bytes};
}

self.addEventListener("install", event => {
  event.waitUntil((async () => {
    const cache = await caches.open(cacheName);
    // When updating a fully downloaded app, keep the old offline installation working
    // until every asset of its replacement is available.
    const previous = (await caches.keys()).filter(name => name.startsWith(prefix) && name !== cacheName);
    const offlineBefore = await Promise.all(previous.map(async name => (await caches.open(name)).match(new URL(".complete", root))));
    const entries = offlineBefore.some(Boolean) ? ASSETS : ASSETS.filter(item => item.shell);
    for (const item of entries) await storeAsset(cache, item);
    if (entries.length === ASSETS.length) await cache.put(new URL(".complete", root), new Response(VERSION));
  })());
});

self.addEventListener("activate", event => {
  event.waitUntil((async () => {
    const old = (await caches.keys()).filter(name => name.startsWith(prefix) && name !== cacheName);
    await Promise.all(old.map(name => caches.delete(name)));
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", event => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  url.search = "";
  const asset = url.href === root ? indexURL : url.href;
  if (!paths.has(asset)) return;
  event.respondWith((async () => (await (await caches.open(cacheName)).match(asset)) || fetch(req))());
});

let preparing;
const ports = new Set();
self.addEventListener("message", event => {
  const port = event.ports[0];
  if (!port || !["status", "prepare"].includes(event.data)) return;
  event.waitUntil((async () => {
    const cache = await caches.open(cacheName);
    const {urls, bytes} = await count(cache);
    if (event.data === "status") {
      port.postMessage({bytes, total, done:bytes === total});
      return;
    }
    ports.add(port);
    const send = message => { for (const peer of ports) peer.postMessage(message); };
    if (!preparing) preparing = (async () => {
      let saved = bytes;
      send({bytes:saved, total});
      for (const item of ASSETS) {
        if (urls.has(new URL(item.path, root).href)) continue;
        await storeAsset(cache, item);
        saved += item.size;
        send({bytes:saved, total});
      }
      await cache.put(new URL(".complete", root), new Response(VERSION));
      send({done:true, bytes:total, total});
    })().catch(() => send({error:"下载未完成。请检查网络和手机剩余空间，再点继续下载。"}))
      .finally(() => {preparing = null; ports.clear();});
    await preparing;
  })());
});
