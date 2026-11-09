"""LangGraph tool-calling loop over the nine shopping tools.

    START → model ──(tool calls)──► tools ──► model … → END
                 └─(plain text)──► END

The model decides *what* to do; ``ToolBox`` decides what is *allowed* (quotas,
risk-site lockout, recommend id validation). Two things are deterministic here
regardless of the model:

* a risky browse result (rate_limited / captcha / login_required / blocked /
  extension offline) ends the turn with an interrupt — never auto-retried;
* the loop is capped at ``agent_max_steps``.
"""
from __future__ import annotations

import json
import time
from typing import Annotated, Any, Optional, TypedDict

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from app import llm
from app.agent_runner import blocked_text, interrupt_response
from app.config import settings
from app.store import Session, store
from app.tools import BLOCKING, ToolBox
from app.trace import log_event

TERMINAL_TOOLS = {"recommend", "ask_user"}

SYSTEM_PROMPT = """你是购物选品助手，通过工具完成：理解需求 → 搜索/抓取 → 比较 → 推荐。

规则：
1. 先看下方「当前状态」。已有缓存商品足够时，用 query_inventory 直接重排，不要重复搜索；用户明确要求重新搜索/换站点时才再搜。
2. 预算、内存等硬约束用 update_requirements 写入（价格统一按人民币 price_cny 比较）。
3. 每站每轮最多 search 2 次、next_page 2 次、fetch_detail 5 次；被拒绝（quota_exceeded）时用已有数据收尾，不要硬试。
4. search 后必须 scrape_listing 才会有商品；对最有希望的至多 3 个商品 fetch_detail 核实规格后再推荐。
5. 浏览工具返回 rate_limited / captcha / login_required / blocked 时系统会自动中断并交给用户，你不需要也不能重试。
6. recommend 只能引用 query_inventory 返回的 product_id，给出 1-3 个，理由要引用具体价格/规格。
7. 必须以 recommend（完成）或 ask_user（信息不足、需用户决定时）结束本轮。不要编造商品、价格或规格。
8. 若缺少预算或品类等关键过滤条件、或硬约束之间明显冲突（如低预算配高内存），先用 clarify 一次性列出全部问题（每题给候选项）；用户的回答会在下一条消息的 answers 里自动并入需求。"""


# --- tool schemas ------------------------------------------------------------


class _NoArgs(BaseModel):
    pass


class _UpdateReq(BaseModel):
    budget_cny: Optional[float] = Field(None, description="预算上限，人民币")
    category: Optional[str] = Field(None, description="品类，如 笔记本")
    use_case: Optional[str] = Field(None, description="用途，如 开发")
    must_have: Optional[list[str]] = Field(None, description="硬性要求，如 ['32G内存']；内存请写成 '<数字>G内存'")
    preferences: Optional[list[str]] = Field(None, description="偏好（非硬性）")


class _QueryInv(BaseModel):
    limit: int = Field(10, ge=1, le=30)


class _Search(BaseModel):
    site: str = Field(description="jd 或 ebay")
    query: str = Field(description="该站点的搜索词（eBay 用英文）")


class _Scrape(BaseModel):
    site: Optional[str] = Field(None, description="默认当前搜索站点")
    page: Optional[int] = Field(None, ge=1)


class _NextPage(BaseModel):
    site: Optional[str] = None


class _Detail(BaseModel):
    product_id: str = Field(description="query_inventory 返回的 product_id")


class _Pick(BaseModel):
    product_id: str
    reason: str = Field(description="具体推荐理由，引用价格/规格")


class _Recommend(BaseModel):
    picks: list[_Pick] = Field(min_length=1, max_length=3)
    answer: str = Field(description="给用户的最终回复（中文，简洁）")


class _Ask(BaseModel):
    question: str
    reason: str = Field("clarify", description="clarify | empty | other")


class _ClarifyQ(BaseModel):
    id: str = Field(description="问题标识，如 budget / category / budget_ram_conflict")
    question: str
    options: list[str] = Field(default_factory=list, description="候选项；用户也可自由输入")


class _Clarify(BaseModel):
    questions: list[_ClarifyQ] = Field(min_length=1, max_length=5)


def _compact_browse(result: dict[str, Any]) -> dict[str, Any]:
    """What the model needs to see — not the raw payload."""
    keep = ("status", "error", "accepted", "product_count", "page", "no_more", "site", "reason", "used", "limit")
    out = {k: result[k] for k in keep if k in result}
    if "product" in result:
        out["product"] = result["product"]
    return out


def build_tools(box: ToolBox, state: dict[str, Any]) -> list[StructuredTool]:
    """Wrap ToolBox methods. ``state`` collects the turn outcome / blocking signal."""

    def note_browse(result: dict[str, Any]) -> dict[str, Any]:
        status = result.get("status")
        if status in BLOCKING and result.get("error") != "quota_exceeded":
            state["blocked"] = result
        return _compact_browse(result)

    async def get_session() -> dict[str, Any]:
        info = box.get_session()
        info.pop("recommendations", None)
        return info

    async def update_requirements(**patch: Any) -> dict[str, Any]:
        return box.update_requirements("", {k: v for k, v in patch.items() if v is not None})

    async def query_inventory(limit: int = 10) -> dict[str, Any]:
        return box.query_inventory(limit=limit)

    async def search(site: str, query: str) -> dict[str, Any]:
        return note_browse(await box.search(site, query))

    async def scrape_listing(site: Optional[str] = None, page: Optional[int] = None) -> dict[str, Any]:
        return note_browse(await box.scrape_listing(site, page))

    async def next_page(site: Optional[str] = None) -> dict[str, Any]:
        return note_browse(await box.next_page(site))

    async def fetch_detail(product_id: str) -> dict[str, Any]:
        return note_browse(await box.fetch_detail(product_id))

    async def recommend(picks: list[dict[str, Any]], answer: str) -> dict[str, Any]:
        picks = [p.model_dump() if hasattr(p, "model_dump") else dict(p) for p in picks]
        result = box.recommend(picks, answer)
        if result.get("ok"):
            state["done"] = {"kind": "recommend", **result}
        return result

    async def ask_user(question: str, reason: str = "clarify") -> dict[str, Any]:
        result = box.ask_user(question, reason)
        state["done"] = {"kind": "ask_user", **result}
        return result

    async def clarify(questions: list[dict[str, Any]]) -> dict[str, Any]:
        questions = [q.model_dump() if hasattr(q, "model_dump") else dict(q) for q in questions]
        result = box.ask_clarify(questions)
        state["done"] = {"kind": "clarify", "questions": questions}
        return result

    def tool(fn, name, description, schema):
        return StructuredTool.from_function(
            coroutine=fn, name=name, description=description, args_schema=schema
        )

    return [
        tool(get_session, "get_session", "查看会话：需求、站点、库存摘要、配额。", _NoArgs),
        tool(update_requirements, "update_requirements", "写入/合并结构化需求（预算、品类、用途、硬性要求、偏好）。", _UpdateReq),
        tool(query_inventory, "query_inventory", "按当前硬约束查询已缓存商品（不访问站点）。返回 product_id/标题/价格/是否已抓详情。", _QueryInv),
        tool(search, "search", "在一个站点打开一个搜索词的结果页。之后需 scrape_listing 才会入库。", _Search),
        tool(scrape_listing, "scrape_listing", "解析当前搜索结果页并把商品写入缓存。", _Scrape),
        tool(next_page, "next_page", "翻到下一页并入库（每站每轮最多 2 次）。", _NextPage),
        tool(fetch_detail, "fetch_detail", "打开一个商品详情页，补全规格与价格（每站每轮最多 5 次）。", _Detail),
        tool(recommend, "recommend", "提交最终推荐（1-3 个已缓存商品）并结束本轮。", _Recommend),
        tool(ask_user, "ask_user", "向用户提问或请求其决定，并结束本轮。", _Ask),
        tool(clarify, "clarify", "关键过滤条件（预算/品类等）不明或有冲突时，一次性列出结构化澄清问题（每题给候选项），用户回答后随下一条消息自动并入需求。", _Clarify),
    ]


# --- graph -------------------------------------------------------------------


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    steps: int


def _context_block(box: ToolBox) -> str:
    info = box.get_session()
    return (
        "当前状态：\n"
        f"- 站点：{info['sites']}（已搜索：{box.session.searched_sites}）\n"
        f"- 需求：{json.dumps(info['requirements'], ensure_ascii=False)}\n"
        f"- {info['inventory_summary']}\n"
        f"- 配额使用：{json.dumps(info['quotas'], ensure_ascii=False)}\n"
        f"- 运行模式：{'mock 数据' if box.mock_mode else '真实浏览器'}"
    )


def _history(session: Session, text: str, limit: int = 10) -> list[AnyMessage]:
    msgs: list[AnyMessage] = []
    for item in session.messages[-limit:]:
        content = str(item.get("content") or "")
        if not content:
            continue
        msgs.append(HumanMessage(content) if item.get("role") == "user" else AIMessage(content))
    if not msgs or not isinstance(msgs[-1], HumanMessage) or msgs[-1].content != text:
        msgs.append(HumanMessage(text))
    return msgs


def _text_of(message: AnyMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content.strip()
    return " ".join(
        part.get("text", "") if isinstance(part, dict) else str(part) for part in content
    ).strip()


async def run_llm_policy(
    session: Session, text: str, box: ToolBox, *, skip_parse: bool = False
) -> dict[str, Any]:
    # Cheap deterministic parse first, so the model starts from known constraints.
    # Skipped when this turn applied clarify answers (they must not be clobbered).
    if not skip_parse:
        box.update_requirements(text)

    state: dict[str, Any] = {}
    tools = build_tools(box, state)
    by_name = {t.name: t for t in tools}
    model = llm.build_chat_model().bind_tools(tools)
    max_steps = settings.agent_max_steps

    async def model_node(s: AgentState) -> dict[str, Any]:
        started = time.perf_counter()
        ai = await model.ainvoke(
            [SystemMessage(SYSTEM_PROMPT + "\n\n" + _context_block(box)), *s["messages"]]
        )
        calls = [c["name"] for c in (getattr(ai, "tool_calls", None) or [])]
        log_event(
            session,
            f"LLM 决策 step={s['steps'] + 1}: {calls or '文本回复'}",
            stage="llm.step",
            source="llm",
            duration_ms=(time.perf_counter() - started) * 1000,
            data={"tool_calls": calls, "text": _text_of(ai)[:200]},
        )
        return {"messages": [ai], "steps": s["steps"] + 1}

    async def tools_node(s: AgentState) -> dict[str, Any]:
        ai = s["messages"][-1]
        out: list[ToolMessage] = []
        for call in ai.tool_calls:
            name, args = call["name"], call.get("args") or {}
            if state.get("done") or state.get("blocked"):
                content: Any = {"error": "turn_already_finished"}
            elif name not in by_name:
                content = {"error": f"unknown tool {name}"}
            else:
                started = time.perf_counter()
                try:
                    content = await by_name[name].ainvoke(args)
                except Exception as exc:  # noqa: BLE001 — bad args etc. go back to the model
                    content = {"error": f"{type(exc).__name__}: {exc}"}
                log_event(
                    session,
                    f"工具 {name} → {_status_of(content)}",
                    stage=f"llm.tool.{name}",
                    source="llm",
                    level="warning" if _status_of(content) in {"error", "blocked"} else "info",
                    duration_ms=(time.perf_counter() - started) * 1000,
                    data={"args": args, "result": content},
                )
            out.append(
                ToolMessage(
                    content=json.dumps(content, ensure_ascii=False, default=str),
                    tool_call_id=call["id"],
                    name=name,
                )
            )
        return {"messages": out}

    def after_model(s: AgentState) -> str:
        return "tools" if getattr(s["messages"][-1], "tool_calls", None) else END

    def after_tools(s: AgentState) -> str:
        if state.get("done") or state.get("blocked") or s["steps"] >= max_steps:
            return END
        return "model"

    graph = StateGraph(AgentState)
    graph.add_node("model", model_node)
    graph.add_node("tools", tools_node)
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", after_model, {"tools": "tools", END: END})
    graph.add_conditional_edges("tools", after_tools, {"model": "model", END: END})
    app = graph.compile()

    final = await app.ainvoke(
        {"messages": _history(session, text), "steps": 0},
        config={"recursion_limit": max_steps * 2 + 4},
    )
    return _finish(session, box, state, final)


def _status_of(content: Any) -> str:
    if isinstance(content, dict):
        if content.get("error"):
            return "error" if content.get("status") is None else str(content.get("status"))
        return str(content.get("status") or ("ok" if content.get("ok", True) else "error"))
    return "ok"


def _finish(session: Session, box: ToolBox, state: dict[str, Any], final: dict[str, Any]) -> dict[str, Any]:
    if state.get("blocked"):
        result = state["blocked"]
        site = str(result.get("site") or (session.sites[0] if session.sites else "站点"))
        ask = box.ask_user(blocked_text(site, result, "浏览"), str(result.get("status")))
        return interrupt_response(session, ask["question"], ask["reason"])

    done = state.get("done")
    if done and done["kind"] == "recommend":
        session.messages.append({"role": "assistant", "content": done["answer"]})
        log_event(
            session,
            "Agent 回合完成",
            stage="agent.done",
            source="agent",
            data={
                "policy": "llm",
                "steps": final.get("steps"),
                "recommendation_count": len(done["recommendations"]),
                "quotas": session.quotas,
            },
        )
        return {
            "session_id": session.id,
            "status": session.status,
            "reply": done["answer"],
            "recommendations": done["recommendations"],
            "interrupted": False,
            "interrupt_reason": None,
        }
    if done and done["kind"] == "ask_user":
        return interrupt_response(session, done["question"], done["reason"])
    if done and done["kind"] == "clarify":
        response = interrupt_response(session, "请先确认以下筛选条件。", "clarify")
        response["clarify"] = done["questions"]
        return response

    # Model answered in plain text, or the step cap was hit.
    last_text = _text_of(final["messages"][-1]) if final.get("messages") else ""
    if final.get("steps", 0) >= settings.agent_max_steps or not last_text:
        ask = box.ask_user("这一轮步骤已达上限仍未得出结论，请缩小需求或点「继续」让我接着做。", "step_limit")
    else:
        ask = box.ask_user(last_text, "clarify")
    return interrupt_response(session, ask["question"], ask["reason"])
