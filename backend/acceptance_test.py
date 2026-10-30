"""最终验收：通过 HTTP 打后端，逐条验证核心能力。

不依赖浏览器，直接测后端 API 契约与业务逻辑。
"""
from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = "http://127.0.0.1:8000"
PASS, FAIL = [], []


def req(path: str, method: str = "GET", body: dict | None = None, timeout: int = 120):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    r = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json; charset=utf-8"} if data else {},
    )
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    return raw


def parse_sse(raw: str) -> list[tuple[str, dict]]:
    out = []
    for block in raw.split("\n\n"):
        if not block.strip():
            continue
        ev, data = "message", []
        for line in block.split("\n"):
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
        if data:
            try:
                out.append((ev, json.loads("".join(data))))
            except json.JSONDecodeError:
                pass
    return out


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  [{detail}]" if detail else ""))


def chat(tid: str, msg: str) -> list[tuple[str, dict]]:
    return parse_sse(req("/api/chat", "POST", {"thread_id": tid, "message": msg}))


def approve(tid: str, ok: bool) -> list[tuple[str, dict]]:
    return parse_sse(req("/api/approve", "POST",
                         {"thread_id": tid, "approved": ok, "comment": "test"}))


def main() -> int:
    tid = f"accept-{datetime.now().strftime('%H%M%S')}"

    print("\n[1] 健康检查 & 网络信息")
    h = json.loads(req("/api/health"))
    check("health.ok", h.get("ok") is True)
    check("工具数量 = 14", len(h.get("tools", [])) == 14, str(len(h.get("tools", []))))
    n = json.loads(req("/api/network"))
    check("返回局域网 IP", bool(n.get("lan_ips")), str(n.get("lan_ips")))

    print("\n[2] 纯对话（不调工具）")
    ev = chat(tid, "你好，你能做什么？")
    check("有 final 回复", any(e == "final" for e, _ in ev))
    check("最终答复非空", any(e == "final" and d.get("content") for e, d in ev))
    check("无 DSML 泄漏", not any("DSML" in str(d.get("content", "")) for e, d in ev if e == "final"))

    print("\n[3] 小额记账（直接执行）")
    ev = chat(tid, "打车花了 28 块，记交通")
    check("调用了工具", any(e == "trace" and d.get("type") == "tool_call" for e, d in ev))
    check("有 final 回复", any(e == "final" and d.get("content") for e, d in ev))

    print("\n[4] 大额支出 → 审批「通过」")
    before = json.loads(req("/api/panel"))["expense_7d"]["total"]
    ev = chat(tid, "买了台 iPad 3999 元，记购物")
    intr = [d for e, d in ev if e == "interrupt"]
    check("触发 interrupt", bool(intr), json.dumps(intr[:1], ensure_ascii=False)[:80])
    if intr:
        ev2 = approve(tid, True)
        after = json.loads(req("/api/panel"))["expense_7d"]["total"]
        check("确认后入账", after > before, f"{before} -> {after}")
        check("审批后有 final", any(e == "final" and d.get("content") for e, d in ev2))
        check("无 DSML 泄漏", not any("DSML" in str(d.get("content", ""))
                                    for e, d in ev2 if e == "final"))

    print("\n[5] 大额支出 → 审批「取消」")
    tid2 = f"{tid}-c"
    before2 = json.loads(req("/api/panel"))["expense_7d"]["total"]
    ev = chat(tid2, "买个 MacBook 8999 元，记购物")
    intr = [d for e, d in ev if e == "interrupt"]
    check("触发 interrupt", bool(intr))
    if intr:
        ev2 = approve(tid2, False)
        after2 = json.loads(req("/api/panel"))["expense_7d"]["total"]
        check("取消后未入账", abs(after2 - before2) < 0.01, f"{before2} -> {after2}")
        check("有取消 trace", any(e == "trace" and d.get("type") == "tool_cancelled"
                                  for e, d in ev2))

    print("\n[6] 查询统计 & 排序")
    ev = chat(tid, "我最近七天花了多少钱？")
    check("调用 query_expense", any(d.get("tool") == "query_expense"
                                    for e, d in ev if e == "trace"))
    panel = json.loads(req("/api/panel"))
    items = panel["expense_7d"].get("items", [])
    times = [i["spent_at"] for i in items]
    check("明细按时间倒序", times == sorted(times, reverse=True), str(times[:3]))
    cats = list(panel["expense_7d"]["by_category"].values())
    check("分类按金额倒序", cats == sorted(cats, reverse=True), str(cats))

    print("\n[7] 待办 & 日程")
    ev = chat(tid, "帮我加个待办：周六交水电费")
    check("add_todo 被调用", any(d.get("tool") == "add_todo" for e, d in ev if e == "trace"))

    print("\n[8] 提醒调度（插入到期提醒并确认触发）")
    sys.path.insert(0, ".")
    from app import db  # noqa: E402
    db.init_db()
    fire = datetime.now(timezone.utc) - timedelta(seconds=2)  # 已过期
    row = db.add_reminder("验收测试提醒", fire.isoformat())
    res = json.loads(req("/api/reminders/check", "POST", {}))
    fired = [x for x in res.get("fired", []) if x["id"] == row["id"]]
    check("到期提醒被触发", bool(fired))

    print("\n[9] 面试助手")
    panel = json.loads(req("/api/panel"))
    itvs = panel.get("interviews") or []
    check("面试数据已导入", len(itvs) >= 4, f"共 {len(itvs)} 场")
    check("面试含准备清单", any(it.get("checklist") for it in itvs))
    check("面试时间已转北京时间", any(":" in (it.get("start_at") or "") for it in itvs),
          (itvs[0].get("start_at") if itvs else ""))

    ev = chat(tid, "我有哪些面试？")
    check("AI 调用面试工具",
          any(d.get("tool") in ("list_interviews", "next_interview")
              for e, d in ev if e == "trace"))
    finals = [d.get("content", "") for e, d in ev if e == "final" and d.get("content")]
    check("AI 答复提到面试内容",
          any(("面试" in c) or ("场" in c) for c in finals), (finals[0][:60] if finals else ""))
    check("无 DSML 泄漏", not any("DSML" in c for c in finals))

    # 查具体某场会议号
    ev = chat(tid, "Allegis Group 那场的会议号是什么？")
    finals = [d.get("content", "") for e, d in ev if e == "final" and d.get("content")]
    check("能查到具体会议号",
          any("218" in c for c in finals), (finals[0][:60] if finals else ""))

    print("\n" + "=" * 50)
    print(f"通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败项:")
        for f in FAIL:
            print("  -", f)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
