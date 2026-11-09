import ast
import json
import os
import re
import uuid

os.environ["SA_DATABASE_URL"] = "memory"  # force in-memory store before app settings import

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app import llm
from app.config import settings
from app.store import store


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch):
    monkeypatch.setattr(settings, "database_url", "memory")
    monkeypatch.setattr(settings, "mock_browser", True)
    monkeypatch.setattr(settings, "extension_token", "")
    monkeypatch.setattr(settings, "extension_id", "")
    store.reset("memory")
    yield
    store.reset("memory")


# --- shared helpers for LLM-policy tests (fake chat model, no network) ---------


def _call(name: str, **args) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": uuid.uuid4().hex, "type": "tool_call"}],
    )


UNTRUSTED_PREFIX = "[UNTRUSTED_TOOL_RESULT]\n"


def _tool_payload(message: ToolMessage) -> dict:
    content = message.content
    if content.startswith(UNTRUSTED_PREFIX):
        content = content[len(UNTRUSTED_PREFIX) :]
    return json.loads(content)


def _last_tool_json(messages) -> dict:
    tool_msgs = [m for m in messages if isinstance(m, ToolMessage)]
    return _tool_payload(tool_msgs[-1])


class FakeModel:
    """Scripted stand-in for the chat model."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def bind_tools(self, _tools):
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        step = self.script.pop(0)
        return step(messages) if callable(step) else step


@pytest.fixture
def fake_llm(monkeypatch):
    holder: dict = {}

    def install(script):
        holder["model"] = FakeModel(script)
        monkeypatch.setattr(llm, "llm_enabled", lambda: True)
        monkeypatch.setattr(llm, "build_chat_model", lambda: holder["model"])
        return holder["model"]

    return install


def _recommend_from_inventory(messages) -> AIMessage:
    items = _last_tool_json(messages).get("items") or []
    picks = []
    for item in items:
        title = item.get("title") or ""
        if any(tok in title for tok in ("配件", "贴纸", "保护膜", "贴膜")):
            continue
        if re.search(r"shop on ebay", title, re.I):
            continue
        price = item.get("price_cny")
        reason = f"约¥{price:.0f}" if price is not None else "符合筛选"
        picks.append({"product_id": item["product_id"], "reason": reason})
        if len(picks) >= 2:
            break
    if not picks and items:
        picks = [{"product_id": items[0]["product_id"], "reason": "缓存中的候选"}]
    answer = "推荐：" + "；".join(p["reason"] for p in picks) if picks else "没有合适商品"
    return _call("recommend", picks=picks, answer=answer)


class MockShopper:
    """Reactive fake LLM that drives a minimal shopping tool loop offline.

    Used by integration tests and smoke evals so the API stays LLM-only without
    requiring a live API key.
    """

    def __init__(self):
        self.calls = 0

    def bind_tools(self, _tools):
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        status = _parse_status(messages)
        last = messages[-1]

        if isinstance(last, HumanMessage):
            text = str(last.content or "")
            if "已回答澄清问题" in text:
                return self._start_collect(status, force_search=False)
            if re.search(r"rate_limit_me|触发限流", text, re.I):
                site = (status["sites"] or ["jd"])[0]
                return _call("search", site=site, query="rate_limit_me")
            if not re.search(r"跳过澄清|不用问|别问了|直接搜|直接开始", text):
                gaps = _requirement_gaps(status["requirements"])
                if gaps:
                    return _call("clarify", questions=gaps)
            force = bool(re.search(r"重新搜索|再搜|换一批|重搜|research|search again", text, re.I))
            return self._start_collect(status, force_search=force)

        tool_msgs = _trailing_tool_messages(messages)
        name = tool_msgs[-1].name if tool_msgs else ""
        data = _tool_payload(tool_msgs[-1]) if tool_msgs else {}

        if name == "search":
            site = data.get("site") or (status["sites"] or ["jd"])[0]
            return _call("scrape_listing", site=site, page=1)
        if name == "scrape_listing":
            missing = [s for s in status["sites"] if s not in status["searched"]]
            if missing:
                return _call("search", site=missing[0], query=_search_query(status))
            return _call("query_inventory", limit=10)
        if name == "query_inventory":
            return _recommend_from_inventory(messages)
        if name == "fetch_detail":
            return _call("query_inventory", limit=10)
        if name in {"update_requirements", "get_session"}:
            return self._start_collect(status, force_search=False)
        # Fallback: ask the user rather than crash the turn.
        return AIMessage(content="这一步我需要你再确认一下需求。")

    def _start_collect(self, status: dict, *, force_search: bool) -> AIMessage:
        missing = [s for s in status["sites"] if s not in status["searched"]]
        if force_search:
            site = (status["sites"] or ["jd"])[0]
            return _call("search", site=site, query=_search_query(status))
        if not missing and status["product_count"] > 0:
            return _call("query_inventory", limit=10)
        site = (missing or status["sites"] or ["jd"])[0]
        return _call("search", site=site, query=_search_query(status))


def _trailing_tool_messages(messages) -> list[ToolMessage]:
    out: list[ToolMessage] = []
    for msg in reversed(messages):
        if isinstance(msg, ToolMessage):
            out.append(msg)
        else:
            break
    return list(reversed(out))


def _parse_status(messages) -> dict:
    sys = next((m for m in messages if isinstance(m, SystemMessage)), None)
    text = str(sys.content) if sys else ""
    sites_m = re.search(r"站点：(.*?)（已搜索：(.*?)）", text)
    sites = ast.literal_eval(sites_m.group(1)) if sites_m else ["jd"]
    searched = ast.literal_eval(sites_m.group(2)) if sites_m else []
    req_m = re.search(r"需求：(\{.*\})", text)
    requirements = json.loads(req_m.group(1)) if req_m else {}
    count_m = re.search(r"总计=(\d+)", text)
    product_count = int(count_m.group(1)) if count_m else (0 if "无已抓商品" in text else 0)
    return {
        "sites": list(sites),
        "searched": list(searched),
        "requirements": requirements,
        "product_count": product_count,
    }


def _search_query(status: dict) -> str:
    req = status.get("requirements") or {}
    category = req.get("category") or "商品"
    use_case = req.get("use_case")
    return f"{category} {use_case}".strip() if use_case else str(category)


def _requirement_gaps(req: dict) -> list[dict]:
    """Mirror the up-front clarify checklist the real model is prompted to follow."""
    questions: list[dict] = []
    budget = req.get("budget_cny")
    category = req.get("category")
    must = req.get("must_have") or []
    ram = None
    for item in must:
        m = re.search(r"(\d{1,3})\s*G内存", str(item))
        if m:
            ram = int(m.group(1))
            break

    if budget is not None and category and ram and ram >= 32 and budget < 4500:
        return [
            {
                "id": "budget_ram_conflict",
                "question": (
                    f"预算约¥{budget:.0f} 与 内存≥{ram}G 同时满足的{category}很少，"
                    "优先保证哪一个？"
                ),
                "options": ["放宽预算", "内存降到16G", "维持硬约束"],
            }
        ]

    if budget is None and not req.get("budget_unlimited"):
        questions.append(
            {
                "id": "budget",
                "question": "这次的预算范围大概是多少？（用于过滤商品价格）",
                "options": ["3000以内", "3000-5000", "5000-8000", "8000-15000", "1.5万以上", "不限预算"],
            }
        )
    if not category:
        questions.append(
            {
                "id": "category",
                "question": "想买什么品类？",
                "options": ["笔记本", "手机", "平板", "耳机", "显示器", "台式机", "其他（请直接输入）"],
            }
        )
    return questions


@pytest.fixture
def mock_shopper(monkeypatch):
    model = MockShopper()
    monkeypatch.setattr(llm, "llm_enabled", lambda: True)
    monkeypatch.setattr(llm, "build_chat_model", lambda: model)
    return model
