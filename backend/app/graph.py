"""LangGraph 状态机：规划 → (审批) → 执行 → 收尾。

拓扑（compile 后）：
    START → planner ─┬─(需审批)─> approval(interrupt) ─> execute → summarize → END
                     ├─(要调工具)─> execute → summarize → END
                     └─(闲聊)───> summarize → END

关键约束（踩过的坑，别改回去）：
1. 每个 tool_call_id 在消息历史里**只能有一条 ToolMessage**，否则 DeepSeek 会报
   "Messages with role 'tool' must be a response to a preceding message with 'tool_calls'"。
   因此 approval 节点只把「用户决定」写进 state 的 decisions 字段，绝不写 ToolMessage。
2. execute 节点负责为每个 tool_call 补一条 ToolMessage（被拒绝的也补一条 cancelled 说明）。
3. 每轮对话末尾必经过 summarize，由 pro 模型输出最终中文答复。
"""
from __future__ import annotations

import json
import operator
import re
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    BaseMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt

from . import config
from .tools import ALL_TOOLS, TOOLS_BY_NAME, needs_approval

# ===================================================================== 模型
_llm_kwargs = dict(
    api_key=config.DEEPSEEK_API_KEY,
    base_url=config.DEEPSEEK_BASE_URL,
    temperature=0.3,
    timeout=60,
    max_retries=2,
)

planner_llm = ChatOpenAI(model=config.MODEL_FAST, **_llm_kwargs)
writer_llm = ChatOpenAI(model=config.MODEL_PRO, **_llm_kwargs)
planner_with_tools = planner_llm.bind_tools(ALL_TOOLS)

SYSTEM_PROMPT = """你是「小管家」，用户的中文生活助理，负责日程、待办、记账、提醒、面试安排五件事。

规则：
1. 涉及增删改查时**必须调用工具**，不要凭想象编造数据。
2. 相对时间（明天、下周一、一小时后）先用 now 工具拿到当前时间，再换算成具体 ISO8601 时间。
3. 一次可以连续调用多个工具；工具返回后不要复述 JSON。
4. 若某个操作被标记为"待确认"，如实告知用户已提交待确认，不要谎称完成。
5. 与上述五件事无关的问题，直接友好作答，不调用工具。

关于面试：
- 用户问"我下一场面试是什么/什么时候" → 调 next_interview
- 问"我有哪些面试 / 面试安排" → 调 list_interviews（默认列出全部，含已结束的）
- 问某场面试的会议号、密码、入会链接、准备清单 → 先 list_interviews 拿到 id，再调 interview_detail
- 所有面试都已结束时，要如实说明"都已完成"，并列出做过哪几场；不要只说"没有面试记录"
- 面试信息是用户的求职隐私，只在用户主动询问时展示，不要主动外泄。
"""

TAIL_PROMPT = """根据以上工具执行结果，用简洁自然的中文给用户最终答复。
要求：
- 先说结论，再补充关键细节（时间/金额/地点/公司/会议号）
- 如果是**查询类**问题（问有哪些、什么时候、多少钱、会议号多少），必须把查到的具体内容列出来，不能只说"已完成"
- 不输出 JSON 或字段名
- 一般不超过 120 字；列表类可适当放宽
如果工具结果里带有 DSML 之类的标签残留，忽略它们，只按已成功执行的操作作答。"""


# ===================================================================== 状态
class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    trace: Annotated[list[dict[str, Any]], operator.add]
    # 审批决定：{call_id: {"approved": bool, "comment": str}}
    decisions: Annotated[dict[str, dict[str, Any]], lambda a, b: {**a, **b}]


# ===================================================================== 辅助
def _latest_ai_with_calls(state: AgentState) -> AIMessage | None:
    for m in reversed(state["messages"]):
        if isinstance(m, AIMessage) and m.tool_calls:
            return m
    return None


def _pending_approvals(msg: AIMessage, decided: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for tc in msg.tool_calls:
        ok, reason = needs_approval(tc["name"], tc["args"])
        if ok and tc.get("id") not in decided:
            out.append({"call_id": tc.get("id"), "tool": tc["name"],
                        "args": tc["args"], "reason": reason})
    return out


# ===================================================================== 节点
def planner(state: AgentState) -> dict[str, Any]:
    """规划节点：fast 模型决定「聊」还是「调工具」。"""
    messages: list[BaseMessage] = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
    resp: AIMessage = planner_with_tools.invoke(messages)
    trace = [{"type": "tool_call", "call_id": tc.get("id"), "tool": tc["name"],
              "args": tc["args"]} for tc in (resp.tool_calls or [])]
    return {"messages": [resp], "trace": trace}


def approval(state: AgentState) -> dict[str, Any]:
    """审批节点：命中的工具调用在这里挂起，等待用户确认（LangGraph interrupt）。

    注意：不写 ToolMessage（见模块 docstring 约束 1）。
    """
    last = _latest_ai_with_calls(state)
    assert last is not None
    pending = _pending_approvals(last, state.get("decisions") or {})

    decision = interrupt({
        "kind": "approval_required",
        "items": [{"tool": p["tool"], "args": p["args"], "reason": p["reason"]} for p in pending],
        "hint": "确认后继续执行；取消则这笔操作作废。",
    })
    approved = bool((decision or {}).get("approved"))
    comment = (decision or {}).get("comment", "")

    new_decisions = {p["call_id"]: {"approved": approved, "comment": comment} for p in pending}
    return {"decisions": new_decisions,
            "trace": [{"type": "approval", "approved": approved, "comment": comment,
                       "items": [{"tool": p["tool"], "args": p["args"]} for p in pending]}]}


def execute(state: AgentState) -> dict[str, Any]:
    """执行节点：为每个 tool_call 生成恰好一条 ToolMessage。"""
    target = _latest_ai_with_calls(state)
    if target is None:
        return {"trace": [{"type": "noop"}]}

    decided: dict[str, dict[str, Any]] = state.get("decisions") or {}
    # 已经存在 ToolMessage 的 call_id（防重复执行）
    answered = {m.tool_call_id for m in state["messages"] if isinstance(m, ToolMessage)}

    results: list[BaseMessage] = []
    trace: list[dict[str, Any]] = []

    for tc in target.tool_calls:
        name, args, call_id = tc["name"], tc["args"], tc.get("id")
        if call_id in answered:
            continue

        d = decided.get(call_id or "")
        if d is not None and not d.get("approved"):
            results.append(ToolMessage(
                content=json.dumps({"ok": False, "cancelled": True,
                                    "error": "用户取消了这笔操作，请勿重复提交。"},
                                   ensure_ascii=False),
                tool_call_id=call_id))
            trace.append({"type": "tool_cancelled", "tool": name, "call_id": call_id})
            continue

        fn = TOOLS_BY_NAME.get(name)
        if fn is None:
            text = json.dumps({"ok": False, "error": f"未知工具 {name}"}, ensure_ascii=False)
            ok = False
        else:
            try:
                raw = fn.invoke(args)
                text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
            except Exception as exc:
                text = json.dumps({"ok": False, "error": f"工具执行失败: {exc}"},
                                  ensure_ascii=False)
            try:
                ok = bool(json.loads(text).get("ok", True))
            except (json.JSONDecodeError, TypeError, AttributeError):
                ok = True

        results.append(ToolMessage(content=text, tool_call_id=call_id))
        trace.append({"type": "tool_result", "tool": name, "call_id": call_id, "ok": ok})

    return {"messages": results, "trace": trace}


def _sanitize(text: str) -> str:
    """清洗模型输出里的 DSML / 特殊 token 泄漏。

    DeepSeek 在工具调用边界偶尔会把内部标记原样吐出来，例如：
        <｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke name="add_expense">...
    这类内容绝不能展示给用户，必须：
    1. 整段包含 DSML 标记时直接丢弃，回退到"根据工具结果生成"的逻辑；
    2. 否则剔除所有 <｜｜...｜｜> 形式的标记。
    """
    if not text:
        return ""
    if "DSML" in text or "｜｜" in text:
        # 整个回复是标记残留 → 视为空，让上层回退
        cleaned = re.sub(r"<\|[^>]*\|>|<｜[^>]*｜>|<｜[^>]*>", "", text)
        cleaned = re.sub(r"（?full-width[^）]*）?", "", cleaned)
        cleaned = cleaned.strip()
        return cleaned
    return text


# 兜底文案：模型抽风时基于工具结果直接生成
def _fallback_reply(state: AgentState) -> str:
    """扫描最近一轮 ToolMessage，生成一个朴素但真实的回复。

    兜底只在模型输出异常时触发，所以要尽量把"查询类"结果也讲清楚，
    不能只说"操作已完成"——那对查询毫无信息量。
    """
    tool_msgs = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    if not tool_msgs:
        return "我这边刚刚没处理好，能再说一次吗？"

    for m in reversed(tool_msgs):
        try:
            d = json.loads(m.content)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(d, dict):
            continue

        # ---- 取消类
        if d.get("cancelled"):
            return "这笔操作已取消，没有写入。需要重新记录的话告诉我。"

        # ---- 单条失败
        if d.get("ok") is False and d.get("error"):
            return f"处理失败：{d['error']}"

        # ---- 下一场面试
        if "next" in d:
            nxt = d.get("next")
            if not nxt:
                return "近期没有待参加的面试了。"
            parts = [f"{nxt.get('start_at', '')} {nxt.get('company', '')}"]
            if nxt.get("position"):
                parts.append(nxt["position"])
            if nxt.get("platform"):
                parts.append(nxt["platform"])
            return "下一场面试：" + " · ".join(p for p in parts if p)

        # ---- 面试详情
        if "interview" in d and isinstance(d["interview"], dict):
            it = d["interview"]
            bits = [f"{it.get('company', '')} {it.get('start_at', '')}"]
            if it.get("platform"):
                bits.append(it["platform"])
            if it.get("meeting_id"):
                bits.append(f"会议号 {it['meeting_id']}")
            if it.get("passcode"):
                bits.append(f"密码 {it['passcode']}")
            return " · ".join(bits)

        # ---- 面试列表
        if "interviews" in d:
            items = d.get("interviews") or []
            if not items:
                return "暂时没有面试安排。"
            lines = [f"共 {len(items)} 场面试："]
            for it in items:
                st = {"done": "已完成", "pending": "待补充", "confirmed": "已确认"}.get(
                    it.get("status", ""), it.get("status", ""))
                lines.append(
                    f"· {it.get('start_at', '')} {it.get('company', '')}"
                    f"{('（' + it['position'] + '）') if it.get('position') else ''} [{st}]"
                )
            return "\n".join(lines)

        # ---- 账单查询
        if "total" in d and "by_category" in d:
            cats = d.get("by_category") or {}
            detail = "、".join(f"{k} ¥{v:.0f}" for k, v in list(cats.items())[:4])
            return f"共 {d.get('count', 0)} 笔，合计 ¥{d.get('total', 0):.2f}" + (
                f"（{detail}）" if detail else "")

        # ---- 提醒列表
        if "reminders" in d:
            items = d.get("reminders") or []
            if not items:
                return "当前没有待触发的提醒。"
            return "待提醒：" + "；".join(
                f"{it.get('fire_at', '')} {it.get('content', '')}" for it in items[:5])

        # ---- 日程列表
        if "events" in d:
            items = d.get("events") or []
            if not items:
                return "这段时间没有日程。"
            return "日程：" + "；".join(
                f"{it.get('start_at', '')} {it.get('title', '')}" for it in items[:5])

        # ---- 待办列表
        if "todos" in d:
            items = d.get("todos") or []
            if not items:
                return "当前没有待办。"
            return "待办：" + "；".join(str(it.get("title", "")) for it in items[:5])

        # ---- 写操作成功
        if d.get("ok"):
            if "amount" in d:
                return f"已记好：¥{d['amount']} {d.get('category', '')}".strip()
            if "title" in d:
                return f"已处理：{d['title']}"
            if "content" in d:
                return f"已处理：{d['content']}"
            return "已处理完成。"

    return "操作已经完成。"


def summarize(state: AgentState) -> dict[str, Any]:
    """收尾节点：pro 模型把结果翻成人话；输出异常时走兜底。"""
    tail = [SystemMessage(content=TAIL_PROMPT)] + state["messages"]
    try:
        resp: AIMessage = writer_llm.invoke(tail)
        text = _sanitize(resp.content if isinstance(resp.content, str) else "")
    except Exception:
        text = ""

    if not text.strip() or text.strip().startswith("<"):
        # 模型输出为空或疑似残留标记 → 用工具结果兜底
        text = _fallback_reply(state)

    return {"messages": [AIMessage(content=text)], "trace": [{"type": "final"}]}


# ===================================================================== 路由
def route_after_planner(state: AgentState) -> Literal["approval", "execute", "summarize"]:
    last = state["messages"][-1]
    if not (isinstance(last, AIMessage) and last.tool_calls):
        return "summarize"
    if _pending_approvals(last, state.get("decisions") or {}):
        return "approval"
    return "execute"


MAX_PLANNER_ROUNDS = 5  # 防止工具链式调用无限循环


def route_after_execute(state: AgentState) -> Literal["planner", "summarize"]:
    """执行完工具后回到 planner 继续规划（支持链式调用），但限制轮数。"""
    rounds = sum(1 for m in state["messages"] if isinstance(m, AIMessage) and m.tool_calls)
    if rounds >= MAX_PLANNER_ROUNDS:
        return "summarize"
    return "planner"


# ===================================================================== 编译
_APP = None
CHECKPOINTER = MemorySaver()


def get_app():
    global _APP
    if _APP is not None:
        return _APP

    g = StateGraph(AgentState)
    g.add_node("planner", planner)
    g.add_node("approval", approval)
    g.add_node("execute", execute)
    g.add_node("summarize", summarize)

    g.add_edge(START, "planner")
    g.add_conditional_edges("planner", route_after_planner,
                            {"approval": "approval", "execute": "execute",
                             "summarize": "summarize"})
    g.add_edge("approval", "execute")
    g.add_conditional_edges("execute", route_after_execute,
                            {"planner": "planner", "summarize": "summarize"})
    g.add_edge("summarize", END)

    _APP = g.compile(checkpointer=CHECKPOINTER)
    return _APP
