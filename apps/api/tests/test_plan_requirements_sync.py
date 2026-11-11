"""Plan metadata must not keep a stale requirement snapshot."""
from __future__ import annotations

import asyncio

from app.domains.shopping.tools import build_shopping_tools
from app.store import store
from app.tools import ToolBox


def test_update_requirements_syncs_plan_metadata():
    session = store.create("需要笔记本", ["ebay"])
    state = {
        "plan": {
            "metadata": {
                "requirements": {"category": None, "budget_cny": None}
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
            spec.args_model(category="笔记本电脑", budget_cny=5000),
            None,
        )
    )

    assert result.ok
    plan_requirements = state["plan"]["metadata"]["requirements"]
    assert plan_requirements["category"] == "笔记本电脑"
    assert plan_requirements["budget_cny"] == 5000
