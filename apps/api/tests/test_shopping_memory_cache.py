"""ShoppingMemory.summarize should cache until session fingerprint changes."""
from __future__ import annotations

import asyncio

from app.domains.shopping.memory import ShoppingMemory
from app.store import store
from app.tools import ToolBox


def test_summarize_reuses_cache_until_requirements_change(monkeypatch):
    session = store.create("笔记本", ["jd"])
    box = ToolBox(session)
    memory = ShoppingMemory(session, box)

    calls = {"n": 0}
    original = __import__(
        "app.domains.shopping.prompt", fromlist=["context_block"]
    ).context_block

    def counting_block(toolbox):
        calls["n"] += 1
        return original(toolbox)

    monkeypatch.setattr(
        "app.domains.shopping.prompt.context_block", counting_block
    )

    first = asyncio.run(memory.summarize(session.id))
    second = asyncio.run(memory.summarize(session.id))
    assert first == second
    assert calls["n"] == 1

    session.requirements["budget_cny"] = 5000
    third = asyncio.run(memory.summarize(session.id))
    assert calls["n"] == 2
    assert "5000" in third or "budget" in third.lower() or third != first
