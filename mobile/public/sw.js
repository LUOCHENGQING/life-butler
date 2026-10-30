/**
 * Service Worker：离线优先缓存
 *
 * 策略：
 * - 页面外壳（HTML/JS/CSS/字体）：stale-while-revalidate，永远能打开
 * - /api/* 的 GET：network-first，成功则缓存；失败回退缓存（离线也能看数据）
 * - /api/* 的 POST：离线直接拒绝（AI 对话必须联网）
 *
 * 这样电脑关机时，手机仍能打开页面、查看已缓存的面试安排 / 日程 / 账本。
 */
const VERSION = 'butler-v1';
const SHELL = `${VERSION}-shell`;
const DATA = `${VERSION}-data`;

const PRECACHE = ['/', '/manifest.json', '/icon.svg'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL).then((c) => c.addAll(PRECACHE).catch(() => undefined))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(
        keys.filter((k) => !k.startsWith(VERSION)).map((k) => caches.delete(k))
      );
      await self.clients.claim();
    })()
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // 只处理同源请求
  if (url.origin !== self.location.origin) return;

  // ---- API：GET 走 network-first（离线回退缓存），非 GET 不缓存
  if (url.pathname.startsWith('/api/')) {
    if (req.method !== 'GET') {
      // POST 等：联网才可用
      event.respondWith(
        fetch(req).catch(
          () =>
            new Response(
              JSON.stringify({
                offline: true,
                message: '电脑端离线，AI 功能暂不可用。查看已同步的数据请打开「我的」页面。',
              }),
              { status: 503, headers: { 'Content-Type': 'application/json' } }
            )
        )
      );
      return;
    }

    // SSE 长连接不能走缓存
    if (url.pathname === '/api/events') return;

    event.respondWith(
      (async () => {
        const cache = await caches.open(DATA);
        // 用工整的 key 存取：忽略 no-store / 查询串差异，保证离线能命中
        const key = new Request(url.pathname, { method: 'GET' });
        try {
          const fresh = await fetch(req);
          if (fresh.ok) {
            try {
              cache.put(key, fresh.clone());
            } catch {
              /* 某些浏览器不允许缓存克隆体，忽略 */
            }
          }
          return fresh;
        } catch {
          const hit = (await cache.match(key)) || (await cache.match(req));
          if (hit) {
            const body = await hit.clone().text();
            try {
              const json = JSON.parse(body);
              json.__offline = true;
              return new Response(JSON.stringify(json), {
                status: 200,
                headers: { 'Content-Type': 'application/json' },
              });
            } catch {
              return hit;
            }
          }
          return new Response(
            JSON.stringify({ offline: true, message: '电脑端离线，暂无缓存数据' }),
            { status: 503, headers: { 'Content-Type': 'application/json' } }
          );
        }
      })()
    );
    return;
  }

  // ---- 页面与静态资源：stale-while-revalidate
  event.respondWith(
    (async () => {
      const cache = await caches.open(SHELL);
      const hit = await cache.match(req);

      const network = fetch(req)
        .then((res) => {
          if (res.ok && req.method === 'GET') cache.put(req, res.clone());
          return res;
        })
        .catch(() => null);

      if (hit) {
        // 后台更新，先返回缓存
        event.waitUntil(network);
        return hit;
      }

      const fresh = await network;
      if (fresh) return fresh;

      // 导航请求兜底到根页面
      if (req.mode === 'navigate') {
        const root = await cache.match('/');
        if (root) return root;
      }
      return new Response('离线中，且该资源未缓存', {
        status: 503,
        headers: { 'Content-Type': 'text/plain; charset=utf-8' },
      });
    })()
  );
});

// 允许页面主动触发「预缓存数据」（上线同步用）
self.addEventListener('message', (event) => {
  const msg = event.data || {};
  if (msg.type === 'PREFETCH' && Array.isArray(msg.urls)) {
    event.waitUntil(
      (async () => {
        const cache = await caches.open(DATA);
        await Promise.all(
          msg.urls.map(async (u) => {
            try {
              const r = await fetch(u);
              if (r.ok) {
                const path = new URL(u, self.location.origin).pathname;
                await cache.put(new Request(path, { method: 'GET' }), r);
              }
            } catch {
              /* ignore */
            }
          })
        );
      })()
    );
  }
});
