from __future__ import annotations

import asyncio

from app.domains.shopping.memory import ShoppingMemory
from app.store import store
from app.tools import ToolBox


def seed_session():
    session = store.create("需要一台开发笔记本", "jd")
    session.requirements = {
        "budget_cny": 5000,
        "must_have": ["32G内存"],
        "category": "笔记本",
    }
    session.products["p1"] = {
        "product_id": "p1",
        "source": "jd",
        "source_product_id": "1001",
        "title": "SECRET PRODUCT TITLE",
        "price_cny": 4800,
        "attributes": {"内存": "32GB"},
    }
    return session


def test_shopping_memory_delegates_requirements_and_inventory(monkeypatch):
    session = seed_session()
    toolbox = ToolBox(session)
    monkeypatch.setattr(
        toolbox,
        "query_inventory",
        lambda limit: {"items": [{"product_id": "p1"}], "limit": limit},
    )
    memory = ShoppingMemory(session, toolbox)

    assert memory.get_requirements() is session.requirements
    assert memory.query_inventory(limit=2) == [{"product_id": "p1"}]


def test_shopping_memory_records_products():
    session = seed_session()
    memory = ShoppingMemory(session, ToolBox(session))

    memory.record_product(
        {
            "product_id": "p2",
            "source": "jd",
            "title": "Another product",
            "price_cny": 100,
        }
    )

    assert session.products["p2"]["title"] == "Another product"


def test_shopping_memory_load_save_query_and_summary():
    session = seed_session()
    memory = ShoppingMemory(session, ToolBox(session))

    asyncio.run(memory.save("run-1", {"note": "checked"}))
    loaded = asyncio.run(memory.load("run-1"))
    assert loaded["session_id"] == session.id
    assert loaded["note"] == "checked"
    assert loaded["requirements"]["budget_cny"] == 5000
    assert asyncio.run(memory.query("SECRET", limit=1))[0]["product_id"] == "p1"

    summary = asyncio.run(memory.summarize("run-1"))
    assert "商品数=1" in summary
    assert "SECRET PRODUCT TITLE" not in summary
