'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

// ---------------------------------------------------------------- 类型
type Step =
  | { t: 'call'; tool: string; args: Record<string, unknown> }
  | { t: 'ok'; tool: string }
  | { t: 'fail'; tool: string }
  | { t: 'cancel'; tool: string }
  | { t: 'approve'; ok: boolean };

type Msg = { id: string; role: 'me' | 'bot'; text: string; steps: Step[]; busy?: boolean };
type ApproveItem = { tool: string; args: Record<string, unknown>; reason: string };
type ApprovePayload = { items: ApproveItem[] };
type Interview = {
  id: number;
  company: string;
  position: string | null;
  start_at: string;
  platform: string | null;
  meeting_id: string | null;
  passcode: string | null;
  join_url: string | null;
  contact: string | null;
  channel: string | null;
  status: 'confirmed' | 'pending' | 'done';
  note: string | null;
  checklist: string[];
};
type Panel = {
  events: { id: number; title: string; start_at: string }[];
  todos: { id: number; title: string; done: boolean; due_at: string | null; priority: string }[];
  expense_7d: { total: number; count: number; by_category: Record<string, number> };
  reminders: { id: number; content: string; fire_at: string }[];
  interviews?: Interview[];
  next_interview?: Interview | null;
  __offline?: boolean;
};
type NetInfo = { lan_ips: string[]; backend_port: number; mobile_port: number };

const LABEL: Record<string, string> = {
  now: '读时间', add_event: '加日程', list_events: '查日程',
  add_todo: '加待办', list_todos: '查待办', complete_todo: '完成待办',
  add_expense: '记账', query_expense: '查支出', list_recent_expenses: '查明细',
  set_reminder: '设提醒', list_reminders: '查提醒',
  list_interviews: '查面试', next_interview: '下一场面试', interview_detail: '面试详情',
};

const STATUS_LABEL: Record<string, string> = {
  confirmed: '已确认', pending: '待补充', done: '已完成',
};

/**
 * 后端地址解析。
 *
 * 默认走**同源**路径（/api/*），由 Next.js 的 rewrites 转发到后端 :8000。
 * 这样不管你是 localhost、局域网 IP 还是 cpolar 公网域名，前端都不用改代码，
 * 也不需要知道后端端口号——公网隧道只暴露一个端口即可。
 *
 * 只有显式设置了 NEXT_PUBLIC_BACKEND_URL 时才直连（用于特殊部署）。
 */
const EXPLICIT_BACKEND = process.env.NEXT_PUBLIC_BACKEND_URL || '';

function backendBase(): string {
  if (EXPLICIT_BACKEND) return EXPLICIT_BACKEND.replace(/\/$/, '');
  return ''; // 空字符串 = 同源，请求 /api/xxx 由 Next 代理
}

const QUICK = [
  { ic: '💼', txt: '我下一场面试是什么时候？' },
  { ic: '📋', txt: '我有哪些面试安排？' },
  { ic: '💰', txt: '中午吃面花了 35 块，记餐饮' },
  { ic: '⏰', txt: '今晚八点提醒我给妈妈打电话' },
  { ic: '📊', txt: '我最近七天花了多少钱？' },
];

const uid = () => Math.random().toString(36).slice(2, 9);

function argText(a: Record<string, unknown>) {
  return Object.entries(a || {})
    .filter(([, v]) => v !== '' && v != null)
    .map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`)
    .join(' ');
}

// ---------------------------------------------------------------- SSE
function splitSSE(buf: string) {
  const out: { ev: string; data: string }[] = [];
  const blocks = buf.split('\n\n');
  const rest = blocks.pop() ?? '';
  for (const b of blocks) {
    if (!b.trim()) continue;
    let ev = 'message';
    const ds: string[] = [];
    for (const line of b.split('\n')) {
      if (line.startsWith('event:')) ev = line.slice(6).trim();
      else if (line.startsWith('data:')) ds.push(line.slice(5).trim());
    }
    if (ds.length) out.push({ ev, data: ds.join('\n') });
  }
  return { out, rest };
}

// ---------------------------------------------------------------- 主组件
export default function MobileApp() {
  const [threadId, setThreadId] = useState('');
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [approve, setApprove] = useState<ApprovePayload | null>(null);
  const [panel, setPanel] = useState<Panel | null>(null);
  const [net, setNet] = useState<NetInfo | null>(null);
  const [online, setOnline] = useState(false);
  const [drawer, setDrawer] = useState<null | 'agenda' | 'money' | 'conn'>(null);
  const [tab, setTab] = useState<'todo' | 'event' | 'money' | 'interview'>('interview');
  const [toast, setToast] = useState('');

  const scroller = useRef<HTMLDivElement>(null);
  const ta = useRef<HTMLTextAreaElement>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const flash = useCallback((m: string) => {
    setToast(m);
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(''), 2600);
  }, []);

  const pull = useCallback(async () => {
    try {
      const r = await fetch(`${backendBase()}/api/panel`, { cache: 'no-store' });
      if (r.ok) setPanel(await r.json());
    } catch { /* 离线静默 */ }
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const h = await fetch(`${backendBase()}/api/health`, { cache: 'no-store' });
        setOnline(h.ok);
      } catch { setOnline(false); }
      try {
        const n = await fetch(`${backendBase()}/api/network`, { cache: 'no-store' });
        if (n.ok) setNet(await n.json());
      } catch { /* ignore */ }
      try {
        const t = await fetch(`${backendBase()}/api/threads/new`, { method: 'POST' });
        const d = await t.json();
        setThreadId(d.thread_id);
      } catch { setThreadId(uid()); }
      pull();
    })();
  }, [pull]);

  // ---- 实时提醒：SSE 长连接，到点震动 + 弹提示
  useEffect(() => {
    let es: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      try {
        es = new EventSource(`${backendBase()}/api/events`);
        es.addEventListener('reminder', (ev) => {
          try {
            const d = JSON.parse((ev as MessageEvent).data);
            // 手机震动（支持的浏览器才有）
            navigator.vibrate?.([200, 100, 200]);
            // 弹一条 bot 消息进聊天流
            setMsgs((p) => [
              ...p,
              { id: uid(), role: 'bot', text: `🔔 提醒：${d.content}`, steps: [] },
            ]);
            flash(`⏰ ${d.content}`);
            pull();
          } catch { /* ignore */ }
        });
        es.onerror = () => {
          es?.close();
          retry = setTimeout(connect, 3000);
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
  }, [pull, flash]);

  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
  }, [msgs, approve]);

  const patch = useCallback((fn: (m: Msg) => Msg) => {
    setMsgs((p) => {
      if (!p.length) return p;
      const c = [...p];
      c[c.length - 1] = fn(c[c.length - 1]);
      return c;
    });
  }, []);

  const consume = useCallback(
    async (res: Response) => {
      if (!res.body) throw new Error('响应为空');
      const rd = res.body.getReader();
      const dec = new TextDecoder();
      let buf = '';
      for (;;) {
        const { done, value } = await rd.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const { out, rest } = splitSSE(buf);
        buf = rest;
        for (const e of out) {
          let d: any;
          try { d = JSON.parse(e.data); } catch { continue; }

          if (e.ev === 'trace') {
            const x = d;
            if (x.type === 'tool_call') {
              patch((m) => ({ ...m, steps: [...m.steps, { t: 'call', tool: x.tool, args: x.args }] }));
            } else if (x.type === 'tool_result') {
              patch((m) => ({ ...m, steps: [...m.steps, x.ok ? { t: 'ok', tool: x.tool } : { t: 'fail', tool: x.tool }] }));
            } else if (x.type === 'tool_cancelled') {
              patch((m) => ({ ...m, steps: [...m.steps, { t: 'cancel', tool: x.tool }] }));
            } else if (x.type === 'approval') {
              patch((m) => ({ ...m, steps: [...m.steps, { t: 'approve', ok: !!x.approved }] }));
            }
          } else if (e.ev === 'final') {
            patch((m) => ({ ...m, text: d.content || '', busy: false }));
          } else if (e.ev === 'interrupt') {
            setApprove(d);
          } else if (e.ev === 'error') {
            patch((m) => ({ ...m, text: (m.text || '') + `\n\n⚠️ ${d.message}`, busy: false }));
          } else if (e.ev === 'done') {
            patch((m) => ({ ...m, busy: false }));
          }
        }
      }
    },
    [patch]
  );

  const send = useCallback(
    async (raw?: string) => {
      const text = (raw ?? input).trim();
      if (!text || busy || !threadId) return;
      setInput('');
      setApprove(null);
      setBusy(true);
      setMsgs((p) => [
        ...p,
        { id: uid(), role: 'me', text, steps: [] },
        { id: uid(), role: 'bot', text: '', steps: [], busy: true },
      ]);
      try {
        const res = await fetch(`${backendBase()}/api/chat`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ thread_id: threadId, message: text }),
        });
        await consume(res);
      } catch (err: any) {
        patch((m) => ({ ...m, text: `⚠️ 连接失败：${err.message}`, busy: false }));
        setOnline(false);
      } finally {
        setBusy(false);
        pull();
      }
    },
    [input, busy, threadId, consume, patch, pull]
  );

  const decide = useCallback(
    async (ok: boolean) => {
      if (!threadId) return;
      setApprove(null);
      setBusy(true);
      setMsgs((p) => [
        ...p,
        { id: uid(), role: 'me', text: ok ? '✅ 确认执行' : '❌ 取消', steps: [] },
        { id: uid(), role: 'bot', text: '', steps: [], busy: true },
      ]);
      try {
        const res = await fetch(`${backendBase()}/api/approve`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ thread_id: threadId, approved: ok, comment: ok ? '手机端确认' : '手机端取消' }),
        });
        await consume(res);
      } catch (err: any) {
        patch((m) => ({ ...m, text: `⚠️ 失败：${err.message}`, busy: false }));
      } finally {
        setBusy(false);
        pull();
      }
    },
    [threadId, consume, patch, pull]
  );

  const reset = async () => {
    setMsgs([]);
    setApprove(null);
    setBusy(false);
    try {
      const r = await fetch(`${backendBase()}/api/threads/new`, { method: 'POST' });
      setThreadId((await r.json()).thread_id);
    } catch { setThreadId(uid()); }
    flash('已开启新对话');
    pull();
  };

  const openDrawer = (d: 'agenda' | 'money' | 'conn') => {
    setDrawer(d);
    if (d === 'conn') fetch(`${backendBase()}/api/network`).then((r) => r.json()).then(setNet).catch(() => {});
    pull();
  };

  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send();
    }
  };

  const lanIp = net?.lan_ips?.[0];

  // ---------------------------------------------------------------- 渲染
  return (
    <div className="app">
      {/* 顶栏 */}
      <header className="topbar">
        <div className="logo">🧑‍🍳</div>
        <div className="titles">
          <h1>小管家</h1>
          <div className="sub">
            <span className={`status-dot ${online ? 'on' : ''}`} />
            {online ? '在线 · DeepSeek' : '未连接'}
          </div>
        </div>
        <div className="top-actions">
          <button className="icon-btn" onClick={() => openDrawer('conn')} aria-label="连接">
            📶
          </button>
          <button className="icon-btn" onClick={() => openDrawer('agenda')} aria-label="面板">
            ☰
          </button>
        </div>
      </header>

      {/* 消息 */}
      <div className="scroller" ref={scroller}>
        {msgs.length === 0 && (
          <div className="welcome">
            <div className="hero">🧑‍🍳</div>
            <h2>今天想让我帮你做点什么？</h2>
            <p>日程 · 待办 · 记账 · 提醒<br />说一句话就行</p>
            <div className="quick">
              {QUICK.map((q) => (
                <button key={q.txt} className="quick-btn" onClick={() => send(q.txt)} disabled={busy}>
                  <span className="ic">{q.ic}</span>
                  <span>{q.txt}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {msgs.map((m, i) => (
          <div key={m.id} className={`row ${m.role === 'me' ? 'me' : 'bot'}`}>
            <div className="av">{m.role === 'me' ? '🙋' : '🤖'}</div>
            <div className="body">
              {m.steps.length > 0 && (
                <div className="steps">
                  {m.steps.map((s, k) => {
                    if (s.t === 'call') {
                      return (
                        <div className="step" key={k}>
                          <span>🔧</span>
                          <span className="tool">{s.tool}</span>
                          <span>{LABEL[s.tool] ?? ''}</span>
                          <span className="args">{argText(s.args)}</span>
                        </div>
                      );
                    }
                    if (s.t === 'ok') return <div className="step ok" key={k}><span>✅</span><span className="tool">{s.tool}</span><span>完成</span></div>;
                    if (s.t === 'fail') return <div className="step bad" key={k}><span>⚠️</span><span className="tool">{s.tool}</span><span>失败</span></div>;
                    if (s.t === 'cancel') return <div className="step cancel" key={k}><span>🚫</span><span className="tool">{s.tool}</span><span>已取消</span></div>;
                    return <div className={`step ${s.ok ? 'ok' : 'cancel'}`} key={k}><span>{s.ok ? '🔓' : '🔒'}</span><span>审批{s.ok ? '通过' : '取消'}</span></div>;
                  })}
                </div>
              )}

              {m.text && <div className="bubble">{m.text}</div>}
              {!m.text && m.busy && (
                <div className="typing"><i /><i /><i /></div>
              )}

              {m.role === 'bot' && approve && i === msgs.length - 1 && (
                <div className="approve">
                  <div className="approve-head"><span>⚠️</span> 需要你确认</div>
                  {approve.items.map((it, k) => (
                    <div className="approve-card" key={k}>
                      {typeof it.args.amount === 'number' && (
                        <div className="approve-amt">¥{Number(it.args.amount).toFixed(2)}</div>
                      )}
                      <div className="approve-meta">
                        {String(it.args.note || it.args.title || LABEL[it.tool] || it.tool)}
                        {typeof it.args.category === 'string' && ` · ${it.args.category}`}
                      </div>
                      <div className="approve-why">{it.reason}</div>
                    </div>
                  ))}
                  <div className="approve-btns">
                    <button className="big-btn go" onClick={() => decide(true)} disabled={busy}>✅ 确认</button>
                    <button className="big-btn no" onClick={() => decide(false)} disabled={busy}>取消</button>
                  </div>
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* 输入 */}
      <div className="composer">
        <div className="input-wrap">
          <textarea
            ref={ta}
            value={input}
            onChange={(e) => {
              setInput(e.target.value);
              e.target.style.height = 'auto';
              e.target.style.height = Math.min(e.target.scrollHeight, 110) + 'px';
            }}
            onKeyDown={onKey}
            placeholder="说一句话…"
            rows={1}
            disabled={busy}
          />
        </div>
        <button className="send" onClick={() => send()} disabled={busy || !input.trim()} aria-label="发送">
          {busy ? '⏳' : '↑'}
        </button>
      </div>

      {/* 抽屉 */}
      {drawer && (
        <>
          <div className="mask" onClick={() => setDrawer(null)} />
          <div className="drawer">
            <div className="grabber" />
            <div className="drawer-head">
              <h2>
                {drawer === 'agenda' ? '我的面板' : drawer === 'money' ? '我的账本' : '连接信息'}
              </h2>
              <button className="icon-btn" style={{ marginLeft: 'auto', width: 32, height: 32, fontSize: 15 }} onClick={() => setDrawer(null)}>
                ✕
              </button>
            </div>

            <div className="drawer-body">
              {/* 连接信息 */}
              {drawer === 'conn' && (
                <>
                  <div className="conn-card">
                    <div className="conn-row">
                      <span className="k">手机访问</span>
                      <span className="v">{lanIp ? `http://${lanIp}:${net?.mobile_port ?? 3001}` : '—'}</span>
                    </div>
                    <div className="conn-row">
                      <span className="k">后端 API</span>
                      <span className="v">{lanIp ? `http://${lanIp}:${net?.backend_port ?? 8000}` : '—'}</span>
                    </div>
                    <div className="conn-row">
                      <span className="k">状态</span>
                      <span className="v" style={{ color: online ? 'var(--green)' : 'var(--red)' }}>
                        {online ? '● 已连接' : '○ 未连接'}
                      </span>
                    </div>
                    <div className="conn-row">
                      <span className="k">会话</span>
                      <span className="v">{threadId ? threadId.slice(0, 8) : '—'}</span>
                    </div>
                  </div>

                  <div className="section-title">怎么用</div>
                  <div className="item" style={{ display: 'block', lineHeight: 1.75, fontSize: 13.5 }}>
                    1. 手机与电脑连<strong>同一个 WiFi</strong><br />
                    2. 手机浏览器打开上面「手机访问」地址<br />
                    3. 点浏览器菜单 →「添加到主屏幕」<br />
                    4. 之后就和一个 App 一样了 🎉
                  </div>

                  <div className="section-title" style={{ marginTop: 20 }}>操作</div>
                  <button className="quick-btn" style={{ width: '100%' }} onClick={reset}>
                    <span className="ic">🔄</span><span>开启新对话</span>
                  </button>
                </>
              )}

              {/* 日程 + 待办 */}
              {drawer === 'agenda' && (
                <>
                  <div className="tabs" style={{ position: 'relative', background: 'transparent', border: 'none', padding: 0, marginBottom: 16, gap: 8 }}>
                    <button className={`tab ${tab === 'interview' ? 'on' : ''}`} onClick={() => setTab('interview')}>
                      <span className="ic">💼</span>面试 {panel?.interviews?.length ?? 0}
                    </button>
                    <button className={`tab ${tab === 'todo' ? 'on' : ''}`} onClick={() => setTab('todo')}>
                      <span className="ic">✅</span>待办 {panel?.todos.filter((t) => !t.done).length ?? 0}
                    </button>
                    <button className={`tab ${tab === 'event' ? 'on' : ''}`} onClick={() => setTab('event')}>
                      <span className="ic">📅</span>日程 {panel?.events.length ?? 0}
                    </button>
                    <button className={`tab ${tab === 'money' ? 'on' : ''}`} onClick={() => setTab('money')}>
                      <span className="ic">💰</span>账本
                    </button>
                  </div>

                  {/* ---------- 面试 ---------- */}
                  {tab === 'interview' && (
                    <>
                      {panel?.interviews?.length ? (
                        panel.interviews.map((it) => {
                          const isNext = panel.next_interview?.id === it.id;
                          return (
                            <div className={`itv-card ${isNext ? 'next' : ''} ${it.status === 'done' ? 'done' : ''}`} key={it.id}>
                              {isNext && <span className="badge-next">下一场</span>}
                              <div className="itv-time">{it.start_at.slice(5, 16)}</div>
                              <div className="itv-company">
                                {it.company}
                                <span className={`itv-status s-${it.status}`}>
                                  {STATUS_LABEL[it.status] ?? it.status}
                                </span>
                              </div>
                              <div className="itv-meta">
                                {it.position && (
                                  <div className="kv"><span className="k">岗位</span><span className="v">{it.position}</span></div>
                                )}
                                {it.platform && (
                                  <div className="kv"><span className="k">平台</span><span className="v">{it.platform}</span></div>
                                )}
                                {it.meeting_id && (
                                  <div className="kv"><span className="k">会议号</span><span className="v">{it.meeting_id}</span></div>
                                )}
                                {it.passcode && (
                                  <div className="kv"><span className="k">密码</span><span className="v">{it.passcode}</span></div>
                                )}
                                {it.contact && (
                                  <div className="kv"><span className="k">联系人</span><span className="v">{it.contact}</span></div>
                                )}
                              </div>

                              {it.join_url && (
                                <a className="itv-join" href={it.join_url} target="_blank" rel="noreferrer">
                                  🔗 打开入会链接
                                </a>
                              )}

                              {it.checklist.length > 0 && (
                                <div className="itv-check">
                                  {it.checklist.slice(0, 5).map((c, i) => (
                                    <div key={i}><span className="ci">○</span>{c}</div>
                                  ))}
                                </div>
                              )}

                              {it.note && <div className="itv-check">{it.note}</div>}
                            </div>
                          );
                        })
                      ) : (
                        <div className="empty">
                          暂无面试安排<br />
                          <span style={{ fontSize: 12 }}>在聊天里问「我有哪些面试」即可</span>
                        </div>
                      )}
                    </>
                  )}

                  {tab === 'todo' && (
                    <>
                      {panel?.todos.length ? panel.todos.map((t) => (
                        <div className="item" key={t.id}>
                          <span className="dot" style={{ background: t.done ? '#3d4a56' : t.priority === 'high' ? 'var(--red)' : 'var(--green)' }} />
                          <span className={`txt ${t.done ? 'checked' : ''}`}>
                            {t.done && <span style={{ color: 'var(--green)', marginRight: 4 }}>✓</span>}
                            {t.title}
                          </span>
                          {t.due_at && <span className="tm">{t.due_at.slice(5, 16)}</span>}
                        </div>
                      )) : <div className="empty">暂无待办</div>}
                    </>
                  )}

                  {tab === 'event' && (
                    <>
                      {panel?.events.length ? panel.events.map((e) => (
                        <div className="item" key={e.id}>
                          <span className="dot" style={{ background: 'var(--blue)' }} />
                          <span className="txt">{e.title}</span>
                          <span className="tm">{e.start_at.slice(5, 16)}</span>
                        </div>
                      )) : <div className="empty">暂无日程</div>}
                    </>
                  )}

                  {tab === 'money' && (
                    <>
                      <div className="big-stat">
                        <div className="num">¥{(panel?.expense_7d.total ?? 0).toFixed(2)}</div>
                        <div className="lbl">近 7 天 · 共 {panel?.expense_7d.count ?? 0} 笔</div>
                      </div>
                      {panel && Object.keys(panel.expense_7d.by_category).length > 0 ? (
                        (() => {
                          const es = Object.entries(panel.expense_7d.by_category).sort((a, b) => b[1] - a[1]);
                          const mx = Math.max(...es.map(([, v]) => v));
                          return es.map(([k, v]) => (
                            <div className="bar" key={k}>
                              <span className="cat">{k}</span>
                              <span className="track">
                                <span className="fill" style={{ width: `${Math.max(5, (v / mx) * 100)}%` }} />
                              </span>
                              <span className="v">¥{v.toFixed(0)}</span>
                            </div>
                          ));
                        })()
                      ) : <div className="empty">还没有记账</div>}
                    </>
                  )}
                </>
              )}

              {/* 账本（快捷入口直接跳到 money tab） */}
              {drawer === 'money' && (
                <>
                  <div className="big-stat">
                    <div className="num">¥{(panel?.expense_7d.total ?? 0).toFixed(2)}</div>
                    <div className="lbl">近 7 天 · 共 {panel?.expense_7d.count ?? 0} 笔</div>
                  </div>
                  <div className="section-title">⏰ 待提醒</div>
                  {panel?.reminders.length ? panel.reminders.map((r) => (
                    <div className="item" key={r.id}>
                      <span className="dot" style={{ background: 'var(--amber)' }} />
                      <span className="txt">{r.content}</span>
                      <span className="tm">{r.fire_at.slice(5, 16)}</span>
                    </div>
                  )) : <div className="empty">暂无提醒</div>}
                </>
              )}
            </div>
          </div>
        </>
      )}

      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}
