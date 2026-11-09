"""BrowserGateway owns mock/live browse dispatch."""
from __future__ import annotations

import asyncio

from app.browser.gateway import BrowserGateway
from app.store import store


def test_gateway_mock_search_returns_ok():
    session = store.create("笔记本", ["jd"])
    gateway = BrowserGateway(session)
    result = asyncio.run(
        gateway.execute("search", {"site": "jd", "query": "笔记本"})
    )
    assert result.get("status") == "ok"
