/**
 * SSE 代理：Next 的 rewrites 会缓冲流式响应，导致 EventSource 收不到实时推送。
 * 这里用原生 Route Handler 手动 pipe 后端 SSE 流，保证数据即时到达浏览器。
 *
 * 运行环境：Node.js runtime（需要直接 fetch 流）。
 */
export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const BACKEND = process.env.BACKEND_URL || 'http://127.0.0.1:8000';

export async function GET(req: Request) {
  const ac = new AbortController();
  // 浏览器断开时中止上游请求，避免后端堆积连接
  req.signal.addEventListener('abort', () => ac.abort());

  let upstream: Response;
  try {
    upstream = await fetch(`${BACKEND}/api/events`, {
      headers: { Accept: 'text/event-stream', 'Cache-Control': 'no-cache' },
      signal: ac.signal,
    });
  } catch {
    return new Response('event: error\ndata: {"message":"backend unreachable"}\n\n', {
      status: 200,
      headers: {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache, no-transform',
        Connection: 'keep-alive',
      },
    });
  }

  if (!upstream.body) {
    return new Response('event: error\ndata: {"message":"no stream"}\n\n', {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
    });
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      'Content-Type': 'text/event-stream; charset=utf-8',
      'Cache-Control': 'no-cache, no-transform',
      Connection: 'keep-alive',
      'X-Accel-Buffering': 'no',
    },
  });
}
