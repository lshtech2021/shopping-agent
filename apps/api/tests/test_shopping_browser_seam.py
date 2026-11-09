"""ShoppingBrowser must use public ToolBox browse entrypoints, not _browse."""
from __future__ import annotations

import asyncio
import inspect

from app.domains.shopping.adapter import ShoppingBrowser
from app.store import store
from app.tools import ToolBox


def test_shopping_browser_execute_source_does_not_call_private_browse():
    source = inspect.getsource(ShoppingBrowser.execute)
    assert "._browse" not in source
    assert "dispatch_browse" in source or "search" in source


def test_dispatch_browse_search_applies_site_guard():
    session = store.create("笔记本", ["jd"])
    box = ToolBox(session)

    result = asyncio.run(
        box.dispatch_browse("search", {"site": "ebay", "query": "laptop"})
    )
    assert result["status"] == "blocked"
    assert result["error"] == "site_not_selected"


def test_shopping_browser_uses_dispatch(monkeypatch):
    session = store.create("笔记本", ["jd"])
    box = ToolBox(session)
    seen: list[tuple[str, dict]] = []

    async def fake_dispatch(tool, args):
        seen.append((tool, dict(args)))
        return {"status": "ok", "tool": tool}

    monkeypatch.setattr(box, "dispatch_browse", fake_dispatch)
    browser = ShoppingBrowser(box)
    result = asyncio.run(
        browser.execute({"tool": "search", "args": {"site": "jd", "query": "x"}})
    )
    assert result["status"] == "ok"
    assert seen == [("search", {"site": "jd", "query": "x"})]
