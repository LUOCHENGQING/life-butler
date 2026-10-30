'use client';

import { useEffect, useState } from 'react';

/** Service Worker 注册 + 在线状态跟踪。 */
export default function SWRegister() {
  const [online, setOnline] = useState(true);

  useEffect(() => {
    // 注册 SW
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker
        .register('/sw.js')
        .then((reg) => {
          // 预缓存核心数据，供离线查看
          if (navigator.onLine) {
            reg.active?.postMessage({
              type: 'PREFETCH',
              urls: ['/api/panel', '/api/health', '/api/network'],
            });
          }
        })
        .catch(() => {
          /* 开发环境或不支持时忽略 */
        });
    }

    const up = () => {
      setOnline(true);
      // 重新上线时立刻刷新数据缓存
      navigator.serviceWorker?.ready
        .then((reg) =>
          reg.active?.postMessage({
            type: 'PREFETCH',
            urls: ['/api/panel', '/api/health', '/api/network'],
          })
        )
        .catch(() => undefined);
    };
    const down = () => setOnline(false);

    setOnline(navigator.onLine);
    window.addEventListener('online', up);
    window.addEventListener('offline', down);
    return () => {
      window.removeEventListener('online', up);
      window.removeEventListener('offline', down);
    };
  }, []);

  if (online) return null;
  return <div className="offline-bar">📴 电脑端已离线 · 只能查看已缓存的数据</div>;
}
