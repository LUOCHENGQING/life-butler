"""后端冒烟测试：不启动 HTTP，直接跑 LangGraph 图。

用法： python smoke_test.py
覆盖：
  1) 纯对话（不调工具）
  2) 小额记账（直接执行，不审批）
  3) 大额记账（触发 interrupt → resume 批准）
  4) 查询统计
"""
from __future__ import annotations

import json
import uuid

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from app import db
from app.graph import get_app


def run_turn(thread_id: str, text: str, resume: dict | None = None) -> tuple[str, str | None]:
    """跑一轮对话，返回 (最终答复, interrupt payload 或 None)。"""
    app = get_app()
    cfg = {"configurable": {"thread_id": thread_id}}
    inputs = Command(resume=resume) if resume else {"messages": [HumanMessage(content=text)]}

    final = ""
    traces: list[dict] = []
    for mode, chunk in app.stream(inputs, cfg, stream_mode=["updates"]):
        for node, delta in (chunk or {}).items():
            if not isinstance(delta, dict):
                print(f"    [节点] {node}: {delta!r}"[:300])
                continue
            for item in (delta or {}).get("trace", []) or []:
                traces.append({"node": node, **item})
            if node == "summarize":
                for m in (delta or {}).get("messages", []) or []:
                    if getattr(m, "content", ""):
                        final = m.content

    snap = app.get_state(cfg)
    intr = getattr(snap, "interrupts", None) or ()
    payload = getattr(intr[0], "value", None) if intr else None

    print(f"\n>>> 用户: {text}")
    for t in traces:
        if t["type"] == "tool_call":
            print(f"    [调用] {t['tool']} {json.dumps(t['args'], ensure_ascii=False)}")
        elif t["type"] == "tool_result":
            print(f"    [结果] {t['tool']} ok={t['ok']}")
        elif t["type"] == "approval":
            print(f"    [审批] approved={t.get('approved')}")
    if payload:
        print(f"    [中断] 需要人工确认: {json.dumps(payload, ensure_ascii=False)}")
    print(f"<<< 小管家: {final}")
    return final, payload


def main() -> None:
    db.init_db()
    tid = str(uuid.uuid4())
    print("=" * 70)
    print("冒烟测试开始  thread=", tid[:8])
    print("=" * 70)

    # 1 纯对话
    run_turn(tid, "你好，你是谁？")

    # 2 小额记账
    run_turn(tid, "中午吃面花了 35 块，帮我记一下，算餐饮")

    # 3 大额记账 → 应触发审批
    _, payload = run_turn(tid, "刚买了台显示器 1299 元，记到购物里")
    if payload:
        print("\n--- 自动批准（模拟用户点确认）---")
        run_turn(tid, "", resume={"approved": True, "comment": "确认购买"})
    else:
        print("\n!! 警告：大额支出没有触发审批中断")

    # 4 统计查询
    run_turn(tid, "我最近七天花了多少钱？")

    # 5 日程
    run_turn(tid, "明天上午九点提醒我开项目周会，地点在公司 3 楼会议室")

    print("\n" + "=" * 70)
    print("数据库落库情况：")
    print("  事件:", len(db.list_events("2000-01-01", "2099-01-01")))
    print("  待办:", len(db.list_todos(only_open=False)))
    print("  提醒:", len(db.list_pending_reminders()))
    print("=" * 70)


if __name__ == "__main__":
    main()
