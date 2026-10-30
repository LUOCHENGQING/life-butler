"""提醒调度器：后台线程每秒扫一次到期提醒，调用广播回调推给所有在线端。

设计要点：
- 纯 threading，不引入 celery/apscheduler 这类重依赖，单机够用。
- 通过回调把「到期提醒」推给 SSE 长连接（见 main.py 的 /api/events）。
- 前端只要挂着这个 SSE，就能收到实时提醒，不需要轮询。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

from . import db

log = logging.getLogger("butler.scheduler")

TZ = timezone(timedelta(hours=8))

# 订阅者：每个打开的前端 SSE 连接注册一个 queue.put 回调
_subscribers: set[Callable[[dict], None]] = set()
_lock = threading.Lock()


def subscribe(cb: Callable[[dict], None]) -> None:
    with _lock:
        _subscribers.add(cb)


def unsubscribe(cb: Callable[[dict], None]) -> None:
    with _lock:
        _subscribers.discard(cb)


def _broadcast(payload: dict) -> None:
    with _lock:
        subs = list(_subscribers)
    for cb in subs:
        try:
            cb(payload)
        except Exception:
            log.exception("提醒广播失败，移除该订阅者")
            unsubscribe(cb)


def check_due_reminders() -> list[dict]:
    """扫描并触发已到期的提醒，返回本次触发的列表（供测试直接调用）。"""
    now = datetime.now(timezone.utc)
    fired: list[dict] = []
    for r in db.list_pending_reminders(limit=200):
        try:
            due = datetime.fromisoformat(r["fire_at"].replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if due <= now:
            db.mark_reminder_fired(r["id"])
            item = {
                "id": r["id"],
                "content": r["content"],
                "fire_at": due.astimezone(TZ).strftime("%Y-%m-%d %H:%M"),
                "fired_at": now.astimezone(TZ).strftime("%H:%M"),
            }
            fired.append(item)
            _broadcast({"kind": "reminder", **item})
    return fired


_stop = threading.Event()
_thread: threading.Thread | None = None


def _loop(interval: float = 1.0) -> None:
    while not _stop.is_set():
        try:
            check_due_reminders()
        except Exception:
            log.exception("提醒扫描异常")
        _stop.wait(interval)


def start(interval: float = 1.0) -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, args=(interval,), daemon=True,
                               name="reminder-scheduler")
    _thread.start()
    log.info("提醒调度器已启动，扫描间隔 %.1fs", interval)


def stop() -> None:
    _stop.set()
