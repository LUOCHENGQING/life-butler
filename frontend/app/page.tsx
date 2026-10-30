'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

// ---------------------------------------------------------------- 类型
type ToolCall = { type: 'tool_call'; tool: string; args: Record<string, unknown>; call_id?: string };
type ToolResult = { type: 'tool_result'; tool: string; ok: boolean; call_id?: string };
type ToolCancelled = { type: 'tool_cancelled'; tool: string; call_id?: string };
type ApprovalTrace = { type: 'approval'; approved: boolean; comment?: string };
type TraceItem = ToolCall | ToolResult | ToolCancelled | ApprovalTrace | { type: 'final' | 'noop' };

type ApprovalItem = { tool: string; args: Record<string, unknown>; reason: string };
type ApprovalPayload = { kind: string; items: ApprovalItem[]; hint?: string };

type Msg = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  trace: TraceItem[];
  streaming?: boolean;
};

type Panel = {
  events: { id: number; title: string; start_at: string; location?: string | null }[];
  todos: { id: number; title: string; done: boolean; due_at?: string | null; priority: string }[];
  expense_7d: { total: number; count: number; by_category: Record<string, number> };
  reminders: { id: number; content: string; fire_at: string }[];
};

const TOOL_LABEL: Record<string, string> = {
  now: '读取当前时间',
  add_event: '添加日程',
  list_events: '查询日程',
  add_todo: '添加待办',
  list_todos: '查询待办',
  complete_todo: '完成待办',
  add_expense: '记录支出',
  query_expense: '统计支出',
  set_reminder: '设置提醒',
  list_reminders: '查询提醒',
};

const SUGGESTIONS = [
  '明天上午九点提醒我开项目周会，地点公司3楼',
  '中午吃面花了 35 块，记餐饮',
  '买了台显示器 1299 元，记购物',
  '我最近七天花了多少钱？',
  '帮我加个待办：周六去超市买牛奶',
];


/**
 * SSE 必须直连后端：Next 的 rewrites 会缓冲流式响应，导致 EventSource 收不到数据。
 * 后端已配置 CORS（允许 localhost 与私网段），这里按当前 host 拼后端地址。
 */
function backendBase(): string {
  if (typeof window === 'undefined') return 'http://127.0.0.1:8000';
  const { protocol, hostname } = window.location;
  return `${protocol}//${hostname}:8000`;
}

function uid() {
  return Math.random().toString(36).slice(2, 10);
}

function fmtArgs(args: Record<string, unknown>) {
  return Object.entries(args || {})
    .filter(([, v]) => v !== '' && v !== null && v !== undefined)
    .map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
    .join('  ');
}

// ---------------------------------------------------------------- SSE 解析
function parseSSEChunk(buf: string): { events: { event: string; data: string }[]; rest: string } {
  const events: { event: string; data: string }[] = [];
  const blocks = buf.split('\n\n');
  const rest = blocks.pop() ?? '';
  for (const block of blocks) {
    if (!block.trim()) continue;
    let ev = 'message';
    const dataLines: string[] = [];
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) ev = line.slice(6).trim();
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
    }
    if (dataLines.length) events.push({ event: ev, data: dataLines.join('\n') });
  }
  return { events, rest };
}

// ---------------------------------------------------------------- 组件
export default function Page() {
  const [threadId, setThreadId] = useState<string>('');
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [approval, setApproval] = useState<ApprovalPayload | null>(null);
  const [panel, setPanel] = useState<Panel | null>(null);
  const [health, setHealth] = useState<{ models: Record<string, string>; tools: string[] } | null>(null);
  // 实时提醒（SSE 推送过来的）
  const [reminders, setReminders] = useState<
    { id: number; content: string; fire_at: string; fired_at?: string }[]
  >([]);
  const [toast, setToast] = useState('');

  const listRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  // ---- 初始化：新建会话 + 拉面板
  const refreshPanel = useCallback(async () => {
    try {
      const r = await fetch(`${backendBase()}/api/panel`, { cache: 'no-store' });
      if (r.ok) setPanel(await r.json());
    } catch {
      /* 后端未起时静默 */
    }
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const h = await fetch(`${backendBase()}/api/health`, { cache: 'no-store' });
        if (h.ok) setHealth(await h.json());
      } catch {
        /* ignore */
      }
      try {
        const r = await fetch(`${backendBase()}/api/threads/new`, { method: 'POST' });
        const d = await r.json();
        setThreadId(d.thread_id);
      } catch {
        setThreadId(uid());
      }
      refreshPanel();
    })();
  }, [refreshPanel]);

  // ---- 实时提醒：挂一条 SSE 长连接，到点弹提示
  useEffect(() => {
    let es: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      try {
        es = new EventSource(`${backendBase()}/api/events`);
        es.addEventListener('reminder', (ev) => {
          try {
            const d = JSON.parse((ev as MessageEvent).data);
            setReminders((prev) => [
              ...prev,
              { id: d.id, content: d.content, fire_at: d.fire_at, fired_at: d.fired_at },
            ]);
            setToast(`⏰ ${d.content}`);
            setTimeout(() => setToast(''), 6000);
            // 尝试桌面通知
            if (typeof Notification !== 'undefined' && Notification.permission === 'granted') {
              new Notification('小管家提醒', { body: d.content });
            }
          } catch {
            /* ignore */
          }
        });
        es.onerror = () => {
          es?.close();
          retry = setTimeout(connect, 3000); // 断线重连
        };
      } catch {
        retry = setTimeout(connect, 3000);
      }
    };
    connect();

    return () => {
      es?.close();
      if (retry) clearTimeout(retry);
    };
  }, []);

  // ---- 自动滚底
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [msgs, approval]);

  // ---- 更新最后一条 assistant 消息
  const patchLast = useCallback((fn: (m: Msg) => Msg) => {
    setMsgs((prev) => {
      if (!prev.length) return prev;
      const copy = [...prev];
      copy[copy.length - 1] = fn(copy[copy.length - 1]);
      return copy;
    });
  }, []);

  // ---- 消费 SSE 流
  const consume = useCallback(
    async (res: Response) => {
      if (!res.body) throw new Error('无响应体');
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const { events, rest } = parseSSEChunk(buffer);
        buffer = rest;

        for (const e of events) {
          let payload: any = null;
          try {
            payload = JSON.parse(e.data);
          } catch {
            continue;
          }

          if (e.event === 'trace') {
            const t = payload as TraceItem;
            if (t.type === 'final') continue;
            patchLast((m) => ({ ...m, trace: [...m.trace, t] }));
          } else if (e.event === 'final') {
            patchLast((m) => ({ ...m, content: payload.content || '', streaming: false }));
          } else if (e.event === 'interrupt') {
            setApproval(payload as ApprovalPayload);
          } else if (e.event === 'error') {
            patchLast((m) => ({
              ...m,
              content: (m.content || '') + `\n\n⚠️ 出错：${payload.message}`,
              streaming: false,
            }));
          } else if (e.event === 'done') {
            patchLast((m) => ({ ...m, streaming: false }));
          }
        }
      }
    },
    [patchLast]
  );

  // ---- 发送消息
  const send = useCallback(
    async (text?: string) => {
      const content = (text ?? input).trim();
      if (!content || busy || !threadId) return;

      setInput('');
      setApproval(null);
      setBusy(true);

      setMsgs((prev) => [
        ...prev,
        { id: uid(), role: 'user', content, trace: [] },
        { id: uid(), role: 'assistant', content: '', trace: [], streaming: true },
      ]);

      const ac = new AbortController();
      abortRef.current = ac;

      try {
        const res = await fetch(`${backendBase()}/api/chat`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ thread_id: threadId, message: content }),
          signal: ac.signal,
        });
        await consume(res);
      } catch (err: any) {
        if (err?.name !== 'AbortError') {
          patchLast((m) => ({ ...m, content: `⚠️ 连接失败：${err.message}`, streaming: false }));
        }
      } finally {
        setBusy(false);
        abortRef.current = null;
        refreshPanel();
      }
    },
    [input, busy, threadId, consume, patchLast, refreshPanel]
  );

  // ---- 审批
  const decide = useCallback(
    async (approved: boolean) => {
      if (!threadId) return;
      setApproval(null);
      setBusy(true);

      setMsgs((prev) => [
        ...prev,
        { id: uid(), role: 'user', content: approved ? '✅ 确认执行' : '❌ 取消操作', trace: [] },
        { id: uid(), role: 'assistant', content: '', trace: [], streaming: true },
      ]);

      try {
        const res = await fetch(`${backendBase()}/api/approve`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ thread_id: threadId, approved, comment: approved ? '用户确认' : '用户取消' }),
        });
        await consume(res);
      } catch (err: any) {
        patchLast((m) => ({ ...m, content: `⚠️ 审批请求失败：${err.message}`, streaming: false }));
      } finally {
        setBusy(false);
        refreshPanel();
      }
    },
    [threadId, consume, patchLast, refreshPanel]
  );

  const newChat = useCallback(async () => {
    abortRef.current?.abort();
    setMsgs([]);
    setApproval(null);
    setBusy(false);
    try {
      const r = await fetch(`${backendBase()}/api/threads/new`, { method: 'POST' });
      const d = await r.json();
      setThreadId(d.thread_id);
    } catch {
      setThreadId(uid());
    }
    refreshPanel();
  }, [refreshPanel]);

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  // ---------------------------------------------------------------- 渲染
  return (
    <main className="shell">
      {/* ============ 左：聊天 ============ */}
      <section className="chat">
        <header className="chat-head">
          <div className="logo">🧑‍🍳</div>
          <div>
            <h1>小管家</h1>
            <div className="sub">每日生活管家 · LangGraph × DeepSeek</div>
          </div>
          <span className={`badge ${health ? 'on' : ''}`}>
            {health ? `● ${health.models.planner} / ${health.models.writer}` : '○ 连接中'}
          </span>
          <button className="btn sm" onClick={newChat} disabled={busy}>
            新对话
          </button>
        </header>

        <div className="msgs" ref={listRef}>
          {msgs.length === 0 && (
            <div style={{ margin: 'auto', textAlign: 'center', color: 'var(--text-dim)' }}>
              <div style={{ fontSize: 40, marginBottom: 12 }}>🧑‍🍳</div>
              <div style={{ fontSize: 15, color: 'var(--text)', marginBottom: 6 }}>今天想让我帮你做点什么？</div>
              <div style={{ fontSize: 12, marginBottom: 18 }}>
                日程 · 待办 · 记账 · 提醒，说一句话就行
              </div>
              <div className="hint-chips" style={{ justifyContent: 'center', maxWidth: 460 }}>
                {SUGGESTIONS.map((s) => (
                  <button key={s} className="chip" onClick={() => send(s)} disabled={busy}>
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {msgs.map((m) => (
            <div key={m.id} className={`msg ${m.role}`}>
              <div className="avatar">{m.role === 'user' ? '🙋' : '🤖'}</div>
              <div style={{ minWidth: 0 }}>
                {m.trace.length > 0 && (
                  <div className="trace">
                    {m.trace.map((t, i) => {
                      if (t.type === 'tool_call') {
                        return (
                          <div className="trace-item" key={i}>
                            <span className="icon">🔧</span>
                            <div className="body">
                              <span className="name">{t.tool}</span>
                              <span style={{ color: 'var(--text-dim)' }}>
                                {' '}
                                · {TOOL_LABEL[t.tool] ?? '调用工具'}
                              </span>
                              <div className="args">{fmtArgs(t.args)}</div>
                            </div>
                          </div>
                        );
                      }
                      if (t.type === 'tool_result') {
                        return (
                          <div className={`trace-item ${t.ok ? 'ok' : 'err'}`} key={i}>
                            <span className="icon">{t.ok ? '✅' : '⚠️'}</span>
                            <div className="body">
                              <span className="name">{t.tool}</span>
                              <span style={{ color: 'var(--text-dim)' }}>
                                {' '}
                                · {t.ok ? '执行成功' : '执行失败'}
                              </span>
                            </div>
                          </div>
                        );
                      }
                      if (t.type === 'tool_cancelled') {
                        return (
                          <div className="trace-item cancel" key={i}>
                            <span className="icon">🚫</span>
                            <div className="body">
                              <span className="name">{t.tool}</span>
                              <span style={{ color: 'var(--text-dim)' }}> · 已取消</span>
                            </div>
                          </div>
                        );
                      }
                      if (t.type === 'approval') {
                        return (
                          <div className={`trace-item ${t.approved ? 'ok' : 'cancel'}`} key={i}>
                            <span className="icon">{t.approved ? '🔓' : '🔒'}</span>
                            <div className="body">
                              人工审批 · {t.approved ? '已确认' : '已取消'}
                            </div>
                          </div>
                        );
                      }
                      return null;
                    })}
                  </div>
                )}

                {(m.content || m.streaming) && (
                  <div className={`bubble ${m.streaming && !m.content ? 'caret' : ''}`}>
                    {m.content || 'thinking…'}
                  </div>
                )}

                {m.role === 'assistant' && approval && m === msgs[msgs.length - 1] && (
                  <ApprovalCard payload={approval} busy={busy} onDecide={decide} />
                )}
              </div>
            </div>
          ))}
        </div>

        <div className="composer">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="说一句话，例如：明天九点提醒我开周会 / 中午吃面花了 35 块"
            rows={1}
            disabled={busy}
          />
          <button className="btn primary" onClick={() => send()} disabled={busy || !input.trim()}>
            {busy ? '处理中…' : '发送 ⏎'}
          </button>
        </div>
      </section>

      {/* ============ 右：面板 ============ */}
      <aside className="panel">
        <div className="card">
          <h2>📅 日程 <span className="n">{panel?.events.length ?? 0}</span></h2>
          {panel?.events.length ? (
            panel.events.map((e) => (
              <div className="row" key={e.id}>
                <span className="dot" style={{ background: 'var(--accent-2)' }} />
                <span className="t">{e.title}</span>
                <span className="meta">{e.start_at.slice(5, 16)}</span>
              </div>
            ))
          ) : (
            <div className="empty">暂无日程</div>
          )}
        </div>

        <div className="card">
          <h2>
            ✅ 待办
            <span className="n">
              {panel?.todos.filter((t) => !t.done).length ?? 0}
              {panel && panel.todos.some((t) => t.done)
                ? ` / ${panel.todos.length}`
                : ''}
            </span>
          </h2>
          {panel?.todos.length ? (
            // 未完成在前（后端已排好序），已完成置灰划线并带勾
            panel.todos.slice(0, 8).map((t) => (
              <div className="row" key={t.id}>
                <span
                  className="dot"
                  style={{
                    background:
                      t.priority === 'high' && !t.done
                        ? 'var(--danger)'
                        : t.done
                          ? '#3d4a56'
                          : 'var(--accent)',
                  }}
                />
                <span className={`t ${t.done ? 'done' : ''}`}>
                  {t.done && <span style={{ color: 'var(--accent)', marginRight: 4 }}>✓</span>}
                  {t.title}
                </span>
                {t.due_at && <span className="meta">{t.due_at.slice(5, 16)}</span>}
              </div>
            ))
          ) : (
            <div className="empty">暂无待办</div>
          )}
        </div>

        <div className="card">
          <h2>💰 近 7 天支出</h2>
          <div className="stat">
            <span className="big">¥{(panel?.expense_7d.total ?? 0).toFixed(2)}</span>
            <span className="unit">共 {panel?.expense_7d.count ?? 0} 笔</span>
          </div>
          {panel && Object.keys(panel.expense_7d.by_category).length > 0 ? (
            <div className="bars">
              {(() => {
                const entries = Object.entries(panel.expense_7d.by_category).sort((a, b) => b[1] - a[1]);
                const max = Math.max(...entries.map(([, v]) => v));
                return entries.map(([k, v]) => (
                  <div className="bar-row" key={k}>
                    <span className="lab">{k}</span>
                    <span className="bar">
                      <span style={{ width: `${Math.max(4, (v / max) * 100)}%` }} />
                    </span>
                    <span className="amt">¥{v.toFixed(0)}</span>
                  </div>
                ));
              })()}
            </div>
          ) : (
            <div className="empty">还没有记账</div>
          )}
        </div>

        <div className="card">
          <h2>⏰ 提醒 <span className="n">{panel?.reminders.length ?? 0}</span></h2>
          {/* 已触发的提醒（本次会话内 SSE 推送过来的） */}
          {reminders
            .slice()
            .reverse()
            .slice(0, 3)
            .map((r) => (
              <div className="row" key={`fired-${r.id}`}>
                <span className="dot" style={{ background: 'var(--danger)' }} />
                <span className="t" style={{ color: 'var(--warn)' }}>
                  🔔 {r.content}
                </span>
                <span className="meta">{r.fired_at ?? ''} 已触发</span>
              </div>
            ))}
          {panel?.reminders.length ? (
            panel.reminders.map((r) => (
              <div className="row" key={r.id}>
                <span className="dot" style={{ background: 'var(--warn)' }} />
                <span className="t">{r.content}</span>
                <span className="meta">{r.fire_at.slice(5, 16)}</span>
              </div>
            ))
          ) : reminders.length === 0 ? (
            <div className="empty">暂无提醒</div>
          ) : null}
        </div>

        <div className="card">
          <h2>⚙️ 系统</h2>
          <div className="row">
            <span className="t">规划模型</span>
            <span className="meta">{health?.models.planner ?? '—'}</span>
          </div>
          <div className="row">
            <span className="t">撰写模型</span>
            <span className="meta">{health?.models.writer ?? '—'}</span>
          </div>
          <div className="row">
            <span className="t">工具数</span>
            <span className="meta">{health?.tools.length ?? 0}</span>
          </div>
          <div className="row">
            <span className="t">会话</span>
            <span className="meta">{threadId ? threadId.slice(0, 8) : '—'}</span>
          </div>
        </div>
      </aside>

      {/* 实时提醒 Toast */}
      {toast && <div className="toast">{toast}</div>}
    </main>
  );
}

// ---------------------------------------------------------------- 审批卡片
function ApprovalCard({
  payload,
  busy,
  onDecide,
}: {
  payload: ApprovalPayload;
  busy: boolean;
  onDecide: (ok: boolean) => void;
}) {
  return (
    <div className="approval" style={{ marginTop: 10 }}>
      <div className="approval-title">
        <span>⚠️</span> 需要你确认
      </div>
      {payload.items.map((it, i) => (
        <div className="approval-item" key={i}>
          <div>
            {typeof it.args.amount === 'number' && (
              <span className="am">¥{Number(it.args.amount).toFixed(2)} </span>
            )}
            <span style={{ color: 'var(--text)' }}>
              {String(it.args.note || it.args.title || TOOL_LABEL[it.tool] || it.tool)}
            </span>
            {typeof it.args.category === 'string' && (
              <span style={{ color: 'var(--text-dim)' }}> · {it.args.category}</span>
            )}
          </div>
          <div className="approval-reason">{it.reason}</div>
        </div>
      ))}
      <div className="approval-actions">
        <button className="btn primary" onClick={() => onDecide(true)} disabled={busy}>
          ✅ 确认执行
        </button>
        <button className="btn danger" onClick={() => onDecide(false)} disabled={busy}>
          取消
        </button>
      </div>
    </div>
  );
}
