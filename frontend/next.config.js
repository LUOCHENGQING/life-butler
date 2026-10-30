/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    // /api/events 由 app/api/events/route.ts 原生处理，其余 /api/* 转发后端。
    return [
      {
        source: '/api/:path((?!events).*)',
        destination: 'http://127.0.0.1:8000/api/:path*',
      },
    ];
  },
};

module.exports = nextConfig;
