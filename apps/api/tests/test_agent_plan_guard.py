"""Structural plan guards; no category-specific rules."""
from __future__ import annotations

import asyncio

from app.domains.shopping.tools import build_shopping_tools
from app.store import store
from app.tools import ToolBox


def _state_with_active_recommend() -> dict:
    return {
        "plan": {
            "steps": [
                {"id": "search", "status": "done"},
                {"id": "verify", "status": "done"},
                {"id": "recommend", "status": "in_progress"},
            ],
            "metadata": {
                "step_roles": {
                    "search": "search",
                    "verify": "verify",
                    "recommend": "recommend",
                }
            },
        }
    }


def test_search_is_skipped_while_recommend_step_is_active():
    session = store.create("generic requirement", "jd")
    box = ToolBox(session)
    calls = []

    async def fake_search(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status": "ok", "page": 1, "site": "jd"}

    box.search = fake_search
    spec = {
        tool.name: tool
        for tool in build_shopping_tools(box, _state_with_active_recommend())
    }["search"]

    result = asyncio.run(
        spec.handler(spec.args_model(query="another query"), None)
    )

    assert result.ok is True
    assert result.data["skipped"] is True
    assert result.data["reason"] == "recommend_step_active"
    assert calls == []


def test_next_page_is_skipped_while_recommend_step_is_active():
    session = store.create("generic requirement", "jd")
    box = ToolBox(session)
    calls = []

    async def fake_next_page(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status": "ok", "page": 2, "site": "jd"}

    box.next_page = fake_next_page
    spec = {
        tool.name: tool
        for tool in build_shopping_tools(box, _state_with_active_recommend())
    }["next_page"]

    result = asyncio.run(spec.handler(spec.args_model(), None))

    assert result.ok is True
    assert result.data["skipped"] is True
    assert result.data["reason"] == "recommend_step_active"
    assert calls == []


def test_empty_inventory_does_not_complete_verification():
    session = store.create("generic requirement", "jd")
    session.requirements = {
        "category": "widget",
        "must_have": ["waterproof"],
        "preferences": [],
    }
    state = {
        "plan": {
            "steps": [
                {"id": "search", "status": "done"},
                {"id": "verify", "status": "in_progress"},
                {"id": "recommend", "status": "pending"},
            ],
            "metadata": {
                "step_roles": {
                    "search": "search",
                    "verify": "verify",
                    "recommend": "recommend",
                }
            },
        }
    }
    spec = {
        tool.name: tool
        for tool in build_shopping_tools(ToolBox(session), state)
    }["query_inventory"]

    result = asyncio.run(spec.handler(spec.args_model(limit=10), None))

    assert result.data["count"] == 0
    assert state["plan"]["steps"][1]["status"] == "in_progress"
    assert state["plan"]["steps"][2]["status"] == "pending"


def test_search_force_allows_an_explicit_new_search():
    session = store.create("generic requirement", "jd")
    box = ToolBox(session)
    calls = []

    async def fake_search(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status": "ok", "page": 1, "site": "jd"}

    box.search = fake_search
    spec = {
        tool.name: tool
        for tool in build_shopping_tools(box, _state_with_active_recommend())
    }["search"]

    result = asyncio.run(
        spec.handler(
            spec.args_model(query="another query", force=True),
            None,
        )
    )

    assert result.ok is True
    assert result.data["status"] == "ok"
    assert len(calls) == 1
