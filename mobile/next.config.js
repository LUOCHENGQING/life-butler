/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    // 所有 /api/* 都转发给本机后端 :8000。
    //
    // 关键：/api/events（SSE 实时提醒）在 app/api/events/route.ts 里手动 pipe，
    // 而 Next 的 rewrites 统一走「afterFiles」，API Route 优先匹配，
    // 所以 SSE 不会被 rewrite 拦截。
    return {
      beforeFiles: [],
      afterFiles: [],
      // fallback 最后执行：只有没被 API Route 接住的才会到这里
      fallback: [
        { source: '/api/:path*', destination: 'http://127.0.0.1:8000/api/:path*' },
      ],
    };
  },
};

module.exports = nextConfig;
