"""Plan metadata must not keep a stale requirement snapshot."""
from __future__ import annotations

import asyncio

from app.domains.shopping.tools import build_shopping_tools
from app.store import store
from app.tools import ToolBox


def test_update_requirements_syncs_plan_metadata():
    session = store.create("需要笔记本", "ebay")
    state = {
        "plan": {
            "metadata": {
                "requirements": {"category": None, "budget": None},
                "search_queries": {"ebay": ["laptop"]},
            }
        }
    }
    specs = {
        spec.name: spec
        for spec in build_shopping_tools(ToolBox(session), state)
    }
    spec = specs["update_requirements"]

    result = asyncio.run(
        spec.handler(
            spec.args_model(category="笔记本电脑", budget=5000),
            None,
        )
    )

    assert result.ok
    metadata = state["plan"]["metadata"]
    assert metadata["requirements"]["category"] == "笔记本电脑"
    assert metadata["requirements"]["budget"] == 5000
    assert metadata["search_queries"] == {}
    assert metadata["queries_stale_from_requirements"] is True


def test_preference_change_keeps_plan_queries():
    session = store.create("需要笔记本", "ebay")
    state = {
        "plan": {
            "metadata": {
                "requirements": {"category": None, "budget": None},
                "search_queries": {"ebay": ["laptop"]},
            }
        }
    }
    specs = {
        spec.name: spec
        for spec in build_shopping_tools(ToolBox(session), state)
    }
    spec = specs["update_requirements"]

    asyncio.run(
        spec.handler(
            spec.args_model(preferences=["颜色都可以"]),
            None,
        )
    )

    metadata = state["plan"]["metadata"]
    assert metadata["search_queries"] == {"ebay": ["laptop"]}
    assert "queries_stale_from_requirements" not in metadata
