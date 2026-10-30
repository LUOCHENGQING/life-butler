# 小管家 · 每日生活管家 Agent

> 一个能真正跑起来的**全栈 AI Agent**：日程 / 待办 / 记账 / 提醒 / 面试。
> 支持**桌面端 + 手机端双端网页**，手机可通过内网穿透在任何网络下访问。

<p align="center">
  <b>LangGraph 状态机</b> · <b>FastAPI</b> · <b>Next.js</b> · <b>SQLite</b> · <b>DeepSeek 双模型</b>
</p>

---

## ✨ 亮点

### 1. LangGraph 状态机（不是一条直线的聊天机器人）

```
START → planner ─┬─(需审批)─→ approval(interrupt) ─→ execute → planner
                 ├─(要调工具)──→ execute → planner
                 └─(闲聊)────→ summarize → END
```

- **planner**：`deepseek-flash` 判断「聊」还是「调工具」
- **approval**：命中风险策略时用 `interrupt()` **真的把图挂起**，等待人工确认
- **execute**：真正写 SQLite
- **summarize**：`deepseek-v4-pro` 把结果翻成人话

**链式调用**：说「明天九点提醒我开会」→ 先 `now` 拿时间 → `add_event` → `set_reminder`，一轮对话连环三步。

### 2. Human-in-the-loop 人工审批

单笔支出 ≥ ¥200（可配置）时，图真的挂起，前端弹出审批卡片：

```
⚠️ 需要你确认
¥1299.00  显示器 · 购物
单笔支出 ¥1299.00 达到审批阈值 ¥200，需要你确认是否入账。
[✅ 确认]  [取消]
```

- 点确认 → 从断点 `resume` 继续，正常入账
- 点取消 → 操作作废，**不入账**，Agent 如实告知（不会谎称完成）

### 3. 实时提醒（真的会响）

后台线程每秒扫描到期提醒 → 通过 SSE 推送 → 桌面端 Toast + 手机端震动。

### 4. 双端 + PWA 离线

| | 电脑端 | 手机端 |
|---|---|---|
| 地址 | localhost:3000 | 局域网 IP:3001 |
| 设计 | 宽屏双栏（聊天 + 实时面板） | 全屏聊天 + 底部抽屉 |
| 特色 | 右侧常驻面板 | 大按钮、震动提醒、**可添加到主屏幕** |
| 离线 | — | Service Worker 缓存，断网也能看数据 |

### 5. 内网穿透（手机外网访问）

集成 [cpolar](https://www.cpolar.com/)，启动脚本自动建隧道，手机 4G/5G 也能访问。

---

## 🚀 快速开始

### 前置要求

- Python 3.10+
- Node.js 18+
- DeepSeek API Key（[申请地址](https://platform.deepseek.com/)）

### 1. 克隆并安装

```bash
git clone https://github.com/LUOCHENGQING/life-butler.git
cd life-butler
```

### 2. 后端

```bash
cd backend
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

**配置密钥**（重要）：

```bash
# 复制模板
cp .env.example .env      # Windows: copy .env.example .env

# 编辑 .env，填入你的密钥
DEEPSEEK_API_KEY=sk-你的密钥
```

启动：

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

> 用 `0.0.0.0` 而不是 `127.0.0.1`，手机才能访问。

### 3. 电脑端网页

```bash
cd frontend
npm install
npx next dev -p 3000
```

打开 http://localhost:3000

### 4. 手机端网页

```bash
cd mobile
npm install
npx next dev -p 3001 -H 0.0.0.0
```

手机连同一 WiFi，浏览器打开 `http://电脑局域网IP:3001`。

### 5. 一键启动（Windows）

```bash
双击 启动.bat
```

自动启动后端 + 电脑端 + 手机端 + 公网隧道。

---

## 📱 手机外网访问（可选）

本项目集成了 [cpolar](https://www.cpolar.com/) 内网穿透。

```bash
# 1. 安装 cpolar（官网下载）
# 2. 注册账号，获取 authtoken
# 3. 配置
cpolar authtoken 你的token

# 4. 启动隧道（指向手机端 3001）
cpolar http 3001
```

会得到一个公网地址，如 `https://xxxx.r6.cpolar.top`，手机在任何网络下都能访问。

> ⚠️ 免费版地址每次重启会变；固定地址需付费版或自有域名。

---

## 📸 界面预览

| 手机端首页 | 面试面板 |
|---|---|
| 全屏聊天 + 快捷入口 | 4 场面试卡片 + 准备清单 |

| 手机端审批 | 实时提醒 |
|---|---|
| 琥珀色审批卡 + 大按钮 | 到点弹进聊天流 + 震动 |

> 截图存放于 `docs/` 目录（如有）。

---

## 🏗️ 项目结构

```
life-butler/
├── 启动.bat                 # Windows 一键启动
├── backend/
│   ├── app/
│   │   ├── config.py        # 配置、审批阈值、CORS
│   │   ├── db.py            # SQLite + 统一 UTC 时间处理
│   │   ├── tools.py         # 14 个工具 + 审批策略
│   │   ├── graph.py         # LangGraph 状态机（核心）
│   │   ├── scheduler.py     # 提醒调度器（后台线程 + SSE 广播）
│   │   └── main.py          # FastAPI 路由
│   ├── smoke_test.py        # 冒烟测试
│   ├── acceptance_test.py   # 27 项验收测试
│   ├── requirements.txt
│   └── .env.example         # 配置模板（不含真实密钥）
├── frontend/                # 电脑端（Next.js :3000）
│   ├── app/page.tsx
│   └── app/globals.css
└── mobile/                  # 手机端（Next.js :3001，PWA）
    ├── app/page.tsx
    ├── app/sw-register.tsx
    └── public/sw.js         # Service Worker
```

---

## 🔌 API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查 + 模型名 + 工具列表 |
| GET | `/api/network` | 本机局域网 IP |
| GET | `/api/panel` | 面板数据（日程/待办/账目/提醒/面试） |
| POST | `/api/chat` | 流式对话（SSE） |
| POST | `/api/approve` | 人工审批，恢复被中断的图 |
| GET | `/api/events` | 实时提醒 SSE 长连接 |
| GET | `/api/threads/{id}/state` | 查看会话状态 |
| POST | `/api/threads/new` | 新建会话 |

### SSE 事件（`/api/chat`）

| event | 含义 |
|---|---|
| `trace` | 工具调用 / 执行结果 / 审批动作 |
| `final` | 最终中文答复 |
| `interrupt` | 需要人工审批 |
| `error` | 执行异常 |
| `done` | 本轮结束 |

---

## 🧰 14 个工具

**生活类**：`now` `add_event` `list_events` `add_todo` `list_todos` `complete_todo` `add_expense` `query_expense` `list_recent_expenses` `set_reminder` `list_reminders`

**面试类**：`list_interviews` `next_interview` `interview_detail`

**加新工具**：在 `tools.py` 写 `@tool` 函数 → 加进 `ALL_TOOLS` → 自动被模型看见。

---

## 🐛 踩过的坑（血泪史）

### 1. 时间必须统一存 UTC

早期有时存 `+08:00`、有时存 `+00:00`，**字符串比较直接错乱**：
`"2026-10-30T12:00"` 字面大于 `"2026-10-30T05:09"`，但实际更早。

**解决**：所有写入走 `db.to_utc_iso()`，查询窗口也转 UTC。

### 2. 一个 `tool_call_id` 只能有一条 `ToolMessage`

审批节点如果也写占位消息，DeepSeek 会报：
```
Messages with role 'tool' must be a response to a preceding message with 'tool_calls'
```

**解决**：审批结果存 state，由 execute 统一出口。

### 3. 执行完工具必须回 planner

否则「先查时间再设提醒」这种链式任务断在第一步。

### 4. LangGraph `stream()` 在 interrupt 时产出元组

要判断 `isinstance(delta, dict)` 再处理，否则：
```
AttributeError: 'tuple' object has no attribute 'get'
```

### 5. DeepSeek 会吐出原始 DSML 标签

`<｜｜DSML｜｜tool_calls>...` 是模型幻觉了不存在的工具。

**解决**：`_sanitize()` + `_fallback_reply()` 兜底。

### 6. Next.js 会缓冲 SSE

`rewrites` 和 Route Handler 都会让 EventSource 收不到数据（实测 `opened:false`）。

**解决**：`/api/events` 用原生 Route Handler 手动 pipe，不走 rewrite。

### 7. 提醒调度器不是摆设

早期 `mark_reminder_fired()` 写了但没人调用，提醒永远不响。

**解决**：`scheduler.py` 后台线程每秒扫描 + 广播。

---

## 🧪 测试

```bash
cd backend

# 冒烟测试（5 个场景）
python smoke_test.py

# 完整验收（27 项）
python acceptance_test.py
```

---

## ⚙️ 配置

`backend/.env`：

| 变量 | 默认 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | — | **必填** |
| `DEEPSEEK_MODEL_FAST` | `deepseek-flash` | 规划用（快） |
| `DEEPSEEK_MODEL_PRO` | `deepseek-v4-pro` | 撰写用（强） |
| `APPROVAL_THRESHOLD_CNY` | `200` | 审批阈值（元） |

---

## 📄 License

MIT

---

## 🙏 致谢

- [LangGraph](https://github.com/langchain-ai/langgraph) — 状态机编排
- [FastAPI](https://github.com/tiangolo/fastapi) — 后端框架
- [Next.js](https://github.com/vercel/next.js) — 前端框架
- [DeepSeek](https://www.deepseek.com/) — 大模型
- [cpolar](https://www.cpolar.com/) — 内网穿透
