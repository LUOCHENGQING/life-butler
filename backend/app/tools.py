"""生活管家工具箱：8 个工具函数 + 1 个高风险审批节点。

工具签名与说明会直接喂给 DeepSeek 做 function calling，所以 docstring 要写得像给模型看的说明书。
所有时间参数统一约定为 ISO8601 字符串（带时区），模型负责把"明早九点"换算成具体时间。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from langchain_core.tools import tool

from . import db
from .config import APPROVAL_THRESHOLD_CNY

TZ = timezone(timedelta(hours=8))  # 中国标准时间；如需换时区改这里


def _now_local() -> datetime:
    return datetime.now(TZ)


def _parse_iso(value: str) -> datetime:
    """宽松解析：支持 '2026-10-31T09:00'、'2026-10-31 09:00'、带 Z 或带 +08:00。"""
    v = value.strip().replace(" ", "T").replace("Z", "+00:00")
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt


def _fmt(dt: datetime) -> str:
    return dt.astimezone(TZ).strftime("%Y-%m-%d %H:%M")


def _fmt_stored(value: str | None) -> str | None:
    """把库里存的 UTC 字符串格式化成北京时间展示。"""
    if not value:
        return None
    return _fmt(_parse_iso(value))


# ===================================================================== 工具
@tool
def now() -> str:
    """获取当前本地时间（北京时间），返回 ISO8601 字符串与星期几。

    在换算"明天""下周一"这类相对时间之前，先调用本工具拿到现在的准确时间。
    """
    n = _now_local()
    weekday = "一二三四五六日"[n.weekday()]
    return json.dumps({"now": n.isoformat(timespec="minutes"), "weekday": f"星期{weekday}"},
                      ensure_ascii=False)


@tool
def add_event(title: str, start_at: str, location: str = "", note: str = "") -> str:
    """添加一条日程。

    Args:
        title: 日程标题，如"项目周会"。
        start_at: 开始时间，ISO8601 格式，**必须带日期与时间**，如 "2026-10-31T09:00"。
        location: 地点，可空。
        note: 备注，可空。
    """
    row = db.add_event(title, start_at, location or None, note or None)
    return json.dumps({"ok": True, "id": row["id"], "title": row["title"],
                       "start_at": _fmt_stored(row["start_at"])}, ensure_ascii=False)


@tool
def list_events(days_ahead: int = 7) -> str:
    """列出从今天起到未来 N 天内的日程。

    Args:
        days_ahead: 向后看多少天，默认 7。
    """
    start = _now_local().replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=max(1, days_ahead))
    rows = db.list_events(start.isoformat(), end.isoformat())
    items = [{"id": r["id"], "title": r["title"],
              "start_at": _fmt_stored(r["start_at"]),
              "location": r["location"], "note": r["note"]} for r in rows]
    return json.dumps({"count": len(items), "events": items}, ensure_ascii=False)


@tool
def add_todo(title: str, due_at: str = "", priority: str = "normal") -> str:
    """添加一条待办。

    Args:
        title: 待办内容，如"买牛奶"。
        due_at: 截止时间，ISO8601；不需要截止期时留空字符串。
        priority: 优先级，取值 normal / high / low，默认 normal。
    """
    prio = priority if priority in {"normal", "high", "low"} else "normal"
    row = db.add_todo(title, due_at or None, prio)
    return json.dumps({"ok": True, "id": row["id"], "title": row["title"],
                       "due_at": _fmt_stored(row["due_at"])}, ensure_ascii=False)


@tool
def list_todos(only_open: bool = True) -> str:
    """列出待办事项。

    Args:
        only_open: True 只看未完成的（默认）；False 列出全部。
    """
    rows = db.list_todos(only_open=only_open)
    items = [{"id": r["id"], "title": r["title"], "done": bool(r["done"]),
              "due_at": _fmt_stored(r["due_at"]),
              "priority": r["priority"]} for r in rows]
    return json.dumps({"count": len(items), "todos": items}, ensure_ascii=False)


@tool
def complete_todo(todo_id: int) -> str:
    """把一条待办标记为完成。

    Args:
        todo_id: 待办 id，必须先通过 list_todos 查到。
    """
    row = db.complete_todo(todo_id)
    if not row:
        return json.dumps({"ok": False, "error": f"不存在 id={todo_id} 的待办"}, ensure_ascii=False)
    return json.dumps({"ok": True, "id": row["id"], "title": row["title"], "done": True},
                      ensure_ascii=False)


@tool
def add_expense(amount: float, category: str, note: str = "", spent_at: str = "") -> str:
    """记一笔支出。

    Args:
        amount: 金额（人民币，正数），如 35.5。
        category: 分类，如 餐饮 / 交通 / 购物 / 娱乐 / 住房 / 医疗 / 其它。
        note: 备注，如"中午吃面"。
        spent_at: 消费时间 ISO8601；留空表示现在。除非用户明确说"昨天/前天"之类的历史时间，否则一律留空。
    """
    if amount <= 0:
        return json.dumps({"ok": False, "error": "金额必须大于 0"}, ensure_ascii=False)
    row = db.add_expense(round(float(amount), 2), category, note or None,
                         spent_at or None)
    return json.dumps({"ok": True, "id": row["id"], "amount": row["amount"],
                       "category": row["category"], "spent_at": _fmt_stored(row["spent_at"])},
                      ensure_ascii=False)


@tool
def query_expense(days_back: int = 7) -> str:
    """统计最近 N 天的支出，返回总额、笔数与分类汇总（明细按时间倒序，最新在前）。

    Args:
        days_back: 往回看多少天，默认 7（传 1 表示今天，30 表示本月）。
    """
    end = _now_local()
    start = end - timedelta(days=max(1, days_back))
    res = db.query_expense(start.isoformat(), end.isoformat())
    items = [{"id": r["id"], "amount": r["amount"], "category": r["category"],
              "note": r["note"], "spent_at": _fmt_stored(r["spent_at"])}
             for r in res["items"]]
    return json.dumps({"total": res["total"], "count": res["count"],
                       "by_category": res["by_category"], "items": items}, ensure_ascii=False)


@tool
def list_recent_expenses(limit: int = 10) -> str:
    """列出最近几笔支出明细（不限时间窗口，按时间倒序），用于查看"我刚记了什么"。

    Args:
        limit: 最多返回多少笔，默认 10。
    """
    rows = db.list_recent_expenses(max(1, min(limit, 50)))
    items = [{"id": r["id"], "amount": r["amount"], "category": r["category"],
              "note": r["note"], "spent_at": _fmt_stored(r["spent_at"])} for r in rows]
    return json.dumps({"count": len(items), "items": items}, ensure_ascii=False)


@tool
def set_reminder(content: str, fire_at: str) -> str:
    """设置一条定时提醒。

    Args:
        content: 提醒内容，如"关火"。
        fire_at: 提醒触发时间，ISO8601，如 "2026-10-30T19:00"。
    """
    fire = _parse_iso(fire_at)
    if fire <= _now_local():
        return json.dumps({"ok": False, "error": "提醒时间必须晚于当前时间"}, ensure_ascii=False)
    row = db.add_reminder(content, fire.isoformat())
    return json.dumps({"ok": True, "id": row["id"], "content": row["content"],
                       "fire_at": _fmt_stored(row["fire_at"])}, ensure_ascii=False)


@tool
def list_reminders() -> str:
    """列出所有尚未触发的提醒（按触发时间从近到远）。"""
    rows = db.list_pending_reminders()
    items = [{"id": r["id"], "content": r["content"],
              "fire_at": _fmt_stored(r["fire_at"])} for r in rows]
    return json.dumps({"count": len(items), "reminders": items}, ensure_ascii=False)


# ===================================================================== 面试助手
_STATUS_LABEL = {"confirmed": "已确认", "pending": "待补充", "done": "已完成"}


def _interview_dict(r: dict[str, Any]) -> dict[str, Any]:
    """把面试行转成给模型/前端看的结构（时间转北京时间）。"""
    try:
        checklist = json.loads(r.get("checklist") or "[]")
    except (json.JSONDecodeError, TypeError):
        checklist = []
    return {
        "id": r["id"],
        "company": r["company"],
        "position": r["position"],
        "start_at": _fmt_stored(r["start_at"]),
        "platform": r["platform"],
        "meeting_id": r["meeting_id"],
        "passcode": r["passcode"],
        "join_url": r["join_url"],
        "contact": r["contact"],
        "channel": r["channel"],
        "status": _STATUS_LABEL.get(r["status"], r["status"]),
        "note": r["note"],
        "checklist": checklist,
    }


@tool
def list_interviews(upcoming_only: bool = False) -> str:
    """列出面试安排（按时间从近到远）。

    默认返回**全部**面试（含已结束的），这样用户问"我有哪些面试"时能看到完整记录。
    只有用户明确说"接下来的/还没面完的"时才传 upcoming_only=True。

    Args:
        upcoming_only: True 只看还没开始的；默认 False 列出全部。
    """
    rows = db.list_interviews(upcoming_only=upcoming_only)
    items = [_interview_dict(r) for r in rows]
    return json.dumps({"count": len(items), "interviews": items}, ensure_ascii=False)


@tool
def next_interview() -> str:
    """查询下一场即将到来的面试（含公司、岗位、时间、平台、会议号、准备清单）。"""
    r = db.next_interview()
    if not r:
        return json.dumps({"ok": True, "next": None, "message": "近期没有待参加的面试了"},
                          ensure_ascii=False)
    return json.dumps({"ok": True, "next": _interview_dict(r)}, ensure_ascii=False)


@tool
def interview_detail(interview_id: int) -> str:
    """查询某场面试的完整详情（含入会链接、会议号、密码、准备清单）。

    Args:
        interview_id: 面试 id，先通过 list_interviews 获取。
    """
    rows = [r for r in db.list_interviews(upcoming_only=False, limit=500)
            if r["id"] == interview_id]
    if not rows:
        return json.dumps({"ok": False, "error": f"不存在 id={interview_id} 的面试"},
                          ensure_ascii=False)
    return json.dumps({"ok": True, "interview": _interview_dict(rows[0])}, ensure_ascii=False)


ALL_TOOLS = [
    now, add_event, list_events, add_todo, list_todos, complete_todo,
    add_expense, query_expense, list_recent_expenses, set_reminder, list_reminders,
    # ---- 面试助手 ----
    list_interviews, next_interview, interview_detail,
]

TOOLS_BY_NAME: dict[str, Any] = {t.name: t for t in ALL_TOOLS}


# ===================================================================== 审批策略
# 哪些工具属于"高风险，需要人确认"，以及判定条件
def needs_approval(tool_name: str, args: dict[str, Any]) -> tuple[bool, str]:
    """返回 (是否需要人工审批, 审批理由)。在工具真正执行之前调用。"""
    if tool_name == "add_expense":
        amount = float(args.get("amount") or 0)
        if amount >= APPROVAL_THRESHOLD_CNY:
            return True, f"单笔支出 ¥{amount:.2f} 达到审批阈值 ¥{APPROVAL_THRESHOLD_CNY:.0f}，需要你确认是否入账。"
    return False, ""
