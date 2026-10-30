"""从 Obsidian 知识库导入面试安排到管家数据库。

数据来源：E:\\知识库\\10-笔记\\求职材料\\面试安排-*.md
导入策略：
- 幂等：按 (company, start_at) 去重，重复运行不会重复插入
- 时间统一转 UTC 存储
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import db  # noqa: E402

# 已经人工核对过的面试数据（来自知识库笔记，已解析成结构）
INTERVIEWS: list[dict] = [
    {
        "company": "迈动云智（岗位方：世通）",
        "position": "Python 开发",
        "start_at": "2026-10-16T16:40:00+08:00",
        "platform": "腾讯会议",
        "meeting_id": "380 930 528",
        "passcode": None,
        "join_url": None,
        "contact": "杜婷婷（HR）",
        "channel": "COOLEJOKER",
        "status": "done",
        "note": "会议室开放窗口 14:00–17:00。入会方式：腾讯会议 App → 头像 → 扫一扫 → 扫二维码入会。",
        "checklist": [
            "提前 5–10 分钟进入会议室，测试麦克风 / 摄像头 / 网络",
            "过一遍简历终版",
            "过一遍自我介绍与项目介绍",
            "重点复习 Python 相关知识点",
            "准备 3–5 个反问 HR 的问题",
        ],
        "source_note": "10-笔记/求职材料/面试安排-迈动云智.md",
    },
    {
        "company": "腾讯会议面试（公司待补）",
        "position": None,
        "start_at": "2026-10-16T11:00:00+08:00",
        "platform": "腾讯会议",
        "meeting_id": None,
        "passcode": None,
        "join_url": None,
        "contact": None,
        "channel": None,
        "status": "pending",
        "note": "会议号 / 密码 / 入会链接待补充。",
        "checklist": [
            "补填腾讯会议链接 / 会议号 / 密码",
            "提前 5–10 分钟入会，测试音频、视频与网络",
            "过一遍简历终版",
            "过一遍自我介绍与项目介绍",
            "翻阅黑马 AI 大模型面试宝典相关章节",
        ],
        "source_note": "10-笔记/求职材料/面试安排-腾讯会议.md",
    },
    {
        "company": "待补充（QQ 邮箱邀约）",
        "position": None,
        "start_at": "2026-10-17T09:00:00+08:00",
        "platform": None,
        "meeting_id": None,
        "passcode": None,
        "join_url": None,
        "contact": None,
        "channel": "QQ 邮箱邀约",
        "status": "pending",
        "note": "2026-10-17（周六）上午，具体时刻待确认；详情在 QQ 邮箱的邀约邮件里。",
        "checklist": [
            "从 QQ 邮箱翻出面试邀约邮件，补全公司 / 岗位 / 时间 / 形式 / 会议号",
            "根据确认的时间调整提醒时刻",
            "提前测试设备（麦克风 / 摄像头 / 网络）",
            "过一遍简历终版与自我介绍",
        ],
        "source_note": "10-笔记/求职材料/面试安排-待补充（10-17 上午）.md",
    },
    {
        "company": "Allegis Group",
        "position": "Agent 开发",
        "start_at": "2026-10-19T14:30:00+08:00",
        "platform": "Microsoft Teams",
        "meeting_id": "218 025 379 206 213",
        "passcode": "Me6C9HM9",
        "join_url": "https://teams.microsoft.com/meet/218025379206213?p=PdRHNtie2rEtnKsPet",
        "contact": "Joey",
        "channel": "英文面试邀约邮件",
        "status": "done",
        "note": "Tenant key: allegisgroup@m.webex.com ｜ Video ID: 115 300 395 6。提前 5 分钟进入。",
        "checklist": [
            "提前 5 分钟入会，测试麦克风 / 摄像头 / 网络",
            "过一遍简历终版",
            "过一遍自我介绍与项目介绍",
            "准备英文自我介绍（对方为英文沟通）",
            "翻阅黑马 AI 大模型面试宝典相关章节",
        ],
        "source_note": "10-笔记/求职材料/面试安排-Allegis Group（Teams）.md",
    },
]


def main() -> int:
    db.init_db()

    existing = db.list_interviews(upcoming_only=False, limit=500)
    seen = {(r["company"], r["start_at"]) for r in existing}
    print(f"库中已有 {len(existing)} 条面试记录\n")

    added = skipped = 0
    for it in INTERVIEWS:
        key = (it["company"], db.to_utc_iso(it["start_at"]))
        if key in seen:
            print(f"  跳过（已存在）: {it['company']} @ {it['start_at']}")
            skipped += 1
            continue
        row = db.add_interview(**it)
        print(f"  导入: {it['company']} @ {it['start_at']}  (id={row['id']})")
        added += 1

    print(f"\n新增 {added} 条，跳过 {skipped} 条")

    print("\n=== 当前面试列表 ===")
    for r in db.list_interviews(upcoming_only=False):
        flag = {"done": "✓", "pending": "…", "confirmed": "●"}.get(r["status"], "?")
        print(f"  [{flag}] {db.to_utc_iso(r['start_at'])[:16]}  {r['company']}"
              f"  {r['position'] or ''}  {r['platform'] or ''}")

    nxt = db.next_interview()
    print(f"\n下一场: {nxt['company'] if nxt else '无'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
