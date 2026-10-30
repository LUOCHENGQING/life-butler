"""FastAPI 服务层：会话、流式对话（SSE）、审批恢复、面板数据。

端点：
  GET  /api/health                     健康检查
  GET  /api/panel                      右侧面板数据（日程/待办/账本/提醒）
  POST /api/chat                       流式对话，返回 SSE 事件流
  POST /api/approve                    人工审批（恢复被 interrupt 的图）
  GET  /api/threads/{thread_id}/state  查看某会话当前状态
"""
from __future__ import annotations

import json
import queue
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command
from pydantic import BaseModel

from . import config, db
from . import scheduler
from .graph import get_app
from .tools import _fmt_stored, ALL_TOOLS

app = FastAPI(title="每日生活管家 API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.FRONTEND_ORIGINS,
    allow_origin_regex=config.FRONTEND_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    scheduler.start(interval=1.0)


@app.on_event("shutdown")
def _shutdown() -> None:
    scheduler.stop()


# ===================================================================== schema
class ChatIn(BaseModel):
    thread_id: str
    message: str


class ApproveIn(BaseModel):
    thread_id: str
    approved: bool
    comment: str = ""


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ===================================================================== 基础
@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "models": {"planner": config.MODEL_FAST, "writer": config.MODEL_PRO},
            "tools": [t.name for t in ALL_TOOLS]}


@app.get("/api/network")
def network_info() -> dict[str, Any]:
    """返回本机局域网 IP，供手机端显示「在手机上打开这个地址」。"""
    import socket

    ips: list[str] = []
    try:
        # 不真正发包，只为让系统选出默认出口网卡
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ips.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass

    # 兜底：枚举所有非回环网卡
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except Exception:
        pass

    return {"lan_ips": ips, "backend_port": 8000,
            "mobile_port": 3001, "desktop_port": 3000}


@app.get("/api/panel")
def panel(days_ahead: int = 7) -> dict[str, Any]:
    """右侧/抽屉面板数据。

    约定：
    - 日程：从今天零点起 days_ahead 天内，按时间正序（最近的先发生）。
    - 待办：未完成在前、按截止时间；已完成排后面。
    - 支出：近 7 天窗口，明细按时间倒序（最新在前），分类按金额倒序。
    - 提醒：未触发，按触发时间正序。
    """
    now = datetime.now(timezone(timedelta(hours=8)))
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=days_ahead)

    events = [{"id": r["id"], "title": r["title"], "start_at": _fmt_stored(r["start_at"]),
               "location": r["location"]}
              for r in db.list_events(day_start.isoformat(), day_end.isoformat())]
    todos = [{"id": r["id"], "title": r["title"], "done": bool(r["done"]),
              "due_at": _fmt_stored(r["due_at"]),
              "priority": r["priority"]} for r in db.list_todos(only_open=False)]
    exp = db.query_expense((now - timedelta(days=7)).isoformat(), now.isoformat())
    reminders = [{"id": r["id"], "content": r["content"], "fire_at": _fmt_stored(r["fire_at"])}
                 for r in db.list_pending_reminders()]
    recent = [{"id": r["id"], "amount": r["amount"], "category": r["category"],
               "note": r["note"], "spent_at": _fmt_stored(r["spent_at"])}
              for r in db.list_recent_expenses(12)]

    # ---- 面试：时间转北京时间展示
    def _itv(r: dict[str, Any]) -> dict[str, Any]:
        try:
            checklist = json.loads(r.get("checklist") or "[]")
        except (json.JSONDecodeError, TypeError):
            checklist = []
        return {
            "id": r["id"], "company": r["company"], "position": r["position"],
            "start_at": _fmt_stored(r["start_at"]), "platform": r["platform"],
            "meeting_id": r["meeting_id"], "passcode": r["passcode"],
            "join_url": r["join_url"], "contact": r["contact"], "channel": r["channel"],
            "status": r["status"], "note": r["note"], "checklist": checklist,
        }

    interviews = [_itv(r) for r in db.list_interviews(upcoming_only=False)]
    _nx = db.next_interview()
    next_itv = _itv(_nx) if _nx else None

    return {
        "events": events,
        "todos": todos,
        "expense_7d": {"total": exp["total"], "count": exp["count"],
                       "by_category": exp["by_category"], "items": recent},
        "reminders": reminders,
        "interviews": interviews,
        "next_interview": next_itv,
        "server_now": _fmt_stored(now.isoformat()),
    }


# ===================================================================== 对话
def _interrupt_payload(snapshot: Any) -> dict[str, Any] | None:
    """从图快照里取出 pending interrupt 的 payload（如果有）。"""
    intr = getattr(snapshot, "interrupts", None) or ()
    if intr:
        first = intr[0]
        return getattr(first, "value", None)
    return None


def _iter_stream(compiled, inputs, cfg):
    """统一包装 compiled.stream 的 updates 输出。

    LangGraph 在 interrupt 时会产出一条 `('__interrupt__', (Interrupt(...),))`，
    其 value 是元组而非 dict，这里必须做类型守卫。
    """
    for chunk in compiled.stream(inputs, cfg, stream_mode=["updates"]):
        # stream_mode 传 list 时返回 (mode, chunk) 元组
        mode, payload = chunk if isinstance(chunk, tuple) and len(chunk) == 2 else ("updates", chunk)
        if mode != "updates":
            continue
        if not isinstance(payload, dict):
            continue
        for node, delta in payload.items():
            if not isinstance(delta, dict):
                continue
            yield node, delta


@app.post("/api/chat")
def chat(body: ChatIn) -> StreamingResponse:
    compiled = get_app()
    cfg = {"configurable": {"thread_id": body.thread_id}}

    def gen():
        try:
            inputs = {"messages": [HumanMessage(content=body.message)], "trace": []}
            for node, delta in _iter_stream(compiled, inputs, cfg):
                for item in delta.get("trace", []) or []:
                    yield _sse("trace", {"node": node, **item})
                if node == "summarize":
                    for m in delta.get("messages", []) or []:
                        if isinstance(m, AIMessage) and m.content:
                            yield _sse("final", {"content": m.content})

            snap = compiled.get_state(cfg)
            payload = _interrupt_payload(snap)
            if payload:
                yield _sse("interrupt", payload)
            yield _sse("done", {"thread_id": body.thread_id})
        except Exception as exc:  # 兜底：前端要能看到错误
            yield _sse("error", {"message": f"{type(exc).__name__}: {exc}"})
            yield _sse("done", {"thread_id": body.thread_id})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/approve")
def approve(body: ApproveIn) -> StreamingResponse:
    compiled = get_app()
    cfg = {"configurable": {"thread_id": body.thread_id}}

    def gen():
        try:
            cmd = Command(resume={"approved": body.approved, "comment": body.comment})
            for node, delta in _iter_stream(compiled, cmd, cfg):
                for item in delta.get("trace", []) or []:
                    yield _sse("trace", {"node": node, **item})
                if node == "summarize":
                    for m in delta.get("messages", []) or []:
                        if isinstance(m, AIMessage) and m.content:
                            yield _sse("final", {"content": m.content})

            snap = compiled.get_state(cfg)
            payload = _interrupt_payload(snap)
            if payload:
                yield _sse("interrupt", payload)
            yield _sse("done", {"thread_id": body.thread_id})
        except Exception as exc:
            yield _sse("error", {"message": f"{type(exc).__name__}: {exc}"})
            yield _sse("done", {"thread_id": body.thread_id})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/threads/{thread_id}/state")
def thread_state(thread_id: str) -> dict[str, Any]:
    compiled = get_app()
    snap = compiled.get_state({"configurable": {"thread_id": thread_id}})
    msgs = [{"role": type(m).__name__, "content": str(m.content)[:500]} for m in snap.values.get("messages", [])]
    return {"thread_id": thread_id, "messages": msgs,
            "interrupt": _interrupt_payload(snap),
            "next": list(snap.next)}


@app.post("/api/threads/new")
def new_thread() -> dict[str, str]:
    return {"thread_id": str(uuid.uuid4())}


# ===================================================================== 实时提醒
@app.get("/api/events")
def events() -> StreamingResponse:
    """SSE 长连接：推送到期的提醒。

    前端挂上这条连接后，提醒到点会收到 {"kind":"reminder", "content":..., ...}
    不需要轮询 /api/panel。
    """
    q: "queue.Queue[dict]" = queue.Queue(maxsize=100)

    def put(payload: dict) -> None:
        try:
            q.put_nowait(payload)
        except queue.Full:
            pass

    def gen():
        scheduler.subscribe(put)
        try:
            yield _sse("ready", {"ok": True})
            while True:
                try:
                    item = q.get(timeout=20)
                    yield _sse("reminder", item)
                except queue.Empty:
                    # 心跳，防止代理/浏览器断开
                    yield ": ping\n\n"
        finally:
            scheduler.unsubscribe(put)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})


@app.post("/api/reminders/check")
def reminders_check_now() -> dict[str, Any]:
    """手动触发一次到期检查（调试/测试用）。"""
    fired = scheduler.check_due_reminders()
    return {"ok": True, "fired": fired}
