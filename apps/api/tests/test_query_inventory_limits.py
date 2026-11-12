"""The fit judge must not judge far more candidates than requested."""
from __future__ import annotations

import asyncio

from app.domains.shopping.tools import build_shopping_tools
from app.store import store
from app.tools import ToolBox


def test_query_inventory_caps_the_fit_judge_window():
    session = store.create("generic requirement", "jd")
    box = ToolBox(session)
    seen = []

    async def fake_ensure_fit(*, limit=None):
        seen.append(limit)
        return None

    box.ensure_fit = fake_ensure_fit
    spec = {
        tool.name: tool
        for tool in build_shopping_tools(box, {})
    }["query_inventory"]

    asyncio.run(spec.handler(spec.args_model(limit=30), None))
    asyncio.run(spec.handler(spec.args_model(limit=15), None))
    asyncio.run(spec.handler(spec.args_model(limit=5), None))

    assert seen == [30, 15, 10]
