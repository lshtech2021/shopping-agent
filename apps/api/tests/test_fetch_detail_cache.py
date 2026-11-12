"""Detail pages should not be reopened when a successful cache exists."""
from __future__ import annotations

import asyncio

from app.store import store
from app.tools import ToolBox


def _seed():
    session = store.create("generic requirement", "ebay")
    session.products["p1"] = {
        "product_id": "p1",
        "source": "ebay",
        "source_product_id": "123",
        "url": "https://www.ebay.com/itm/123",
        "title": "cached product",
        "price": 100.0,
        "currency": "CNY",
        "shipping_cost": 0,
        "shipping_currency": "CNY",
        "landed_cost": 100.0,
        "cost_complete": True,
        "detail_fetched": True,
        "attributes": {},
    }
    return session


def test_cached_detail_is_returned_without_browsing():
    session = _seed()
    box = ToolBox(session)
    calls = []

    async def fake_browse(tool, args):
        calls.append((tool, args))
        return {"status": "ok"}

    box._browse = fake_browse

    result = asyncio.run(box.fetch_detail("p1"))

    assert result["status"] == "ok"
    assert result["cached"] is True
    assert result["product"]["product_id"] == "p1"
    assert calls == []


def test_force_refreshes_a_cached_detail():
    session = _seed()
    box = ToolBox(session)
    calls = []

    async def fake_browse(tool, args):
        calls.append((tool, args))
        return {
            "status": "ok",
            "title": "refreshed product",
            "price": 120,
            "currency": "CNY",
            "shipping_cost": 0,
            "shipping_currency": "CNY",
        }

    box._browse = fake_browse

    result = asyncio.run(box.fetch_detail("p1", force=True))

    assert result["status"] == "ok"
    assert result.get("cached") is not True
    assert [tool for tool, _args in calls] == ["fetch_detail"]
