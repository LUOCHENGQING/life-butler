"""SQLite 数据层：日程 / 待办 / 账本 / 提醒 四张表。

设计要点：
- 全部使用 UTC 时间戳（ISO8601 字符串），展示层再转本地时区。
- 每个写操作都返回结构化 dict，方便直接塞进 LLM 的 tool 返回值。
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL,
    start_at    TEXT    NOT NULL,
    location    TEXT,
    note        TEXT,
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS todos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL,
    done        INTEGER NOT NULL DEFAULT 0,
    due_at      TEXT,
    priority    TEXT    NOT NULL DEFAULT 'normal',
    created_at  TEXT    NOT NULL,
    done_at     TEXT
);

CREATE TABLE IF NOT EXISTS expenses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    amount      REAL    NOT NULL,
    category    TEXT    NOT NULL,
    note        TEXT,
    spent_at    TEXT    NOT NULL,
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS reminders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    content     TEXT    NOT NULL,
    fire_at     TEXT    NOT NULL,
    fired       INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_start ON events(start_at);
CREATE INDEX IF NOT EXISTS idx_expenses_spent ON expenses(spent_at);
CREATE INDEX IF NOT EXISTS idx_reminders_fire ON reminders(fire_at);

-- ===================== 面试助手 =====================
CREATE TABLE IF NOT EXISTS interviews (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    company      TEXT    NOT NULL,           -- 机构/公司
    position     TEXT,                        -- 岗位
    start_at     TEXT    NOT NULL,            -- 面试时间（UTC 存储）
    platform     TEXT,                        -- 平台：腾讯会议 / Teams / 现场
    meeting_id   TEXT,                        -- 会议号
    passcode     TEXT,                        -- 入会密码
    join_url     TEXT,                        -- 入会链接
    contact      TEXT,                        -- HR / 联系人
    channel      TEXT,                        -- 渠道
    status       TEXT    NOT NULL DEFAULT 'confirmed',  -- confirmed / pending / done
    note         TEXT,                        -- 补充说明
    checklist    TEXT,                        -- JSON 数组：面试前准备清单
    source_note  TEXT,                        -- 来源笔记路径
    created_at   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_interviews_start ON interviews(start_at);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def to_utc_iso(value: str) -> str:
    """把任意带时区的 ISO8601 字符串统一成 UTC 存储格式。

    ⚠️ 关键约束：数据库里所有时间列**必须**统一存 UTC（+00:00）。
    否则 '2026-10-30T12:00:00+08:00' 和 '2026-10-30T05:09:19+00:00'
    做字符串比较会完全错乱（前者字面更大，实际更早）。
    """
    if not value:
        return utcnow()
    v = value.strip().replace(" ", "T").replace("Z", "+00:00")
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        # 无时区信息时按本地时区（+08:00）理解
        dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


# ---------------------------------------------------------------- events
def add_event(title: str, start_at: str, location: str | None = None,
              note: str | None = None) -> dict[str, Any]:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO events(title, start_at, location, note, created_at) VALUES (?,?,?,?,?)",
            (title, to_utc_iso(start_at), location, note, utcnow()),
        )
        row = conn.execute("SELECT * FROM events WHERE id=?", (cur.lastrowid,)).fetchone()
        return _row_to_dict(row)


def list_events(start_from: str, start_to: str) -> list[dict[str, Any]]:
    """按时间窗口列出日程。入参自动转 UTC，出参已排序（最近在前）。"""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE start_at >= ? AND start_at <= ? ORDER BY start_at ASC",
            (to_utc_iso(start_from), to_utc_iso(start_to)),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def list_upcoming_events(limit: int = 10) -> list[dict[str, Any]]:
    """列出从现在起的所有未来日程（不限窗口）。"""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE start_at >= ? ORDER BY start_at ASC LIMIT ?",
            (utcnow(), limit),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------- todos
def add_todo(title: str, due_at: str | None = None, priority: str = "normal") -> dict[str, Any]:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO todos(title, due_at, priority, created_at) VALUES (?,?,?,?)",
            (title, to_utc_iso(due_at) if due_at else None, priority, utcnow()),
        )
        row = conn.execute("SELECT * FROM todos WHERE id=?", (cur.lastrowid,)).fetchone()
        return _row_to_dict(row)


def list_todos(only_open: bool = True, limit: int = 200) -> list[dict[str, Any]]:
    """列出待办：未完成的排前面，同一状态内按截止时间近的在前，无截止期的按最新创建在前。"""
    sql = "SELECT * FROM todos"
    if only_open:
        sql += " WHERE done=0"
    sql += (" ORDER BY done ASC,"
            " CASE WHEN due_at IS NULL THEN 1 ELSE 0 END ASC,"
            " due_at ASC,"
            " created_at DESC, id DESC"
            f" LIMIT {int(limit)}")
    with connect() as conn:
        return [_row_to_dict(r) for r in conn.execute(sql).fetchall()]


def complete_todo(todo_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        conn.execute("UPDATE todos SET done=1, done_at=? WHERE id=?", (utcnow(), todo_id))
        row = conn.execute("SELECT * FROM todos WHERE id=?", (todo_id,)).fetchone()
        return _row_to_dict(row) if row else None


# ---------------------------------------------------------------- expenses
def add_expense(amount: float, category: str, note: str | None,
                spent_at: str | None = None) -> dict[str, Any]:
    """记一笔支出。spent_at 可为空（表示现在），否则统一转 UTC 存储。"""
    when = to_utc_iso(spent_at) if spent_at else utcnow()
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO expenses(amount, category, note, spent_at, created_at) VALUES (?,?,?,?,?)",
            (amount, category, note, when, utcnow()),
        )
        row = conn.execute("SELECT * FROM expenses WHERE id=?", (cur.lastrowid,)).fetchone()
        return _row_to_dict(row)


def query_expense(start: str, end: str) -> dict[str, Any]:
    """统计时间窗口内支出。入参转 UTC；明细按时间倒序（最新在前）。"""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM expenses WHERE spent_at >= ? AND spent_at <= ?"
            " ORDER BY spent_at DESC, id DESC",
            (to_utc_iso(start), to_utc_iso(end)),
        ).fetchall()
        by_cat: dict[str, float] = {}
        for r in rows:
            by_cat[r["category"]] = round(by_cat.get(r["category"], 0.0) + r["amount"], 2)
        # 分类汇总按金额从大到小排序，前端展示更直观
        by_cat_sorted = dict(sorted(by_cat.items(), key=lambda kv: kv[1], reverse=True))
        return {
            "total": round(sum(r["amount"] for r in rows), 2),
            "count": len(rows),
            "by_category": by_cat_sorted,
            "items": [_row_to_dict(r) for r in rows],
        }


def list_recent_expenses(limit: int = 20) -> list[dict[str, Any]]:
    """最近 N 笔支出（不限窗口，按时间倒序）。"""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM expenses ORDER BY spent_at DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------- reminders
def add_reminder(content: str, fire_at: str) -> dict[str, Any]:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO reminders(content, fire_at, created_at) VALUES (?,?,?)",
            (content, to_utc_iso(fire_at), utcnow()),
        )
        row = conn.execute("SELECT * FROM reminders WHERE id=?", (cur.lastrowid,)).fetchone()
        return _row_to_dict(row)


def list_pending_reminders(limit: int = 50) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM reminders WHERE fired=0 ORDER BY fire_at ASC LIMIT ?", (limit,)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def mark_reminder_fired(reminder_id: int) -> None:
    with connect() as conn:
        conn.execute("UPDATE reminders SET fired=1 WHERE id=?", (reminder_id,))


# ---------------------------------------------------------------- interviews
def add_interview(
    company: str,
    position: str | None,
    start_at: str,
    platform: str | None = None,
    meeting_id: str | None = None,
    passcode: str | None = None,
    join_url: str | None = None,
    contact: str | None = None,
    channel: str | None = None,
    status: str = "confirmed",
    note: str | None = None,
    checklist: list[str] | None = None,
    source_note: str | None = None,
) -> dict[str, Any]:
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO interviews
               (company, position, start_at, platform, meeting_id, passcode, join_url,
                contact, channel, status, note, checklist, source_note, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (company, position, to_utc_iso(start_at), platform, meeting_id, passcode,
             join_url, contact, channel, status, note,
             json.dumps(checklist or [], ensure_ascii=False), source_note, utcnow()),
        )
        row = conn.execute("SELECT * FROM interviews WHERE id=?", (cur.lastrowid,)).fetchone()
        return _row_to_dict(row)


def list_interviews(upcoming_only: bool = False, limit: int = 50) -> list[dict[str, Any]]:
    """面试列表：按时间正序（最近的先到）。"""
    sql = "SELECT * FROM interviews"
    params: list[Any] = []
    if upcoming_only:
        sql += " WHERE start_at >= ?"
        params.append(utcnow())
    sql += " ORDER BY start_at ASC LIMIT ?"
    params.append(limit)
    with connect() as conn:
        return [_row_to_dict(r) for r in conn.execute(sql, params).fetchall()]


def next_interview() -> dict[str, Any] | None:
    """下一场还没开始的面试。"""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM interviews WHERE start_at >= ? ORDER BY start_at ASC LIMIT 1",
            (utcnow(),),
        ).fetchone()
        return _row_to_dict(row) if row else None


def update_interview_status(interview_id: int, status: str) -> dict[str, Any] | None:
    with connect() as conn:
        conn.execute("UPDATE interviews SET status=? WHERE id=?", (status, interview_id))
        row = conn.execute("SELECT * FROM interviews WHERE id=?", (interview_id,)).fetchone()
        return _row_to_dict(row) if row else None
