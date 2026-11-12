"""A search page must be scraped before another search replaces it."""
from __future__ import annotations

import asyncio

from app.store import store
from app.tools import ToolBox


def test_search_requires_scrape_before_the_next_search():
    session = store.create("generic requirement", "ebay")
    box = ToolBox(session)
    calls = []

    async def fake_browse(tool, args):
        calls.append((tool, args))
        if tool == "search":
            return {"status": "ok", "page": 1, "site": "ebay"}
        return {"status": "ok", "items": [], "page": 1, "site": "ebay"}

    box._browse = fake_browse

    first = asyncio.run(box.search("ebay", "first query"))
    assert first["status"] == "ok"
    assert box.search_pending_scrape is True

    skipped = asyncio.run(box.search("ebay", "second query"))
    assert skipped["status"] == "skipped"
    assert skipped["reason"] == "search_pending_scrape"
    assert [tool for tool, _args in calls] == ["search"]

    scraped = asyncio.run(box.scrape_listing("ebay"))
    assert scraped["status"] == "ok"
    assert box.search_pending_scrape is False

    third = asyncio.run(box.search("ebay", "third query"))
    assert third["status"] == "ok"
    assert [tool for tool, _args in calls] == [
        "search",
        "scrape_listing",
        "search",
    ]


def test_next_page_also_consumes_the_pending_search():
    session = store.create("generic requirement", "ebay")
    box = ToolBox(session)

    async def fake_browse(tool, _args):
        if tool == "search":
            return {"status": "ok", "page": 1, "site": "ebay"}
        return {
            "status": "ok",
            "items": [],
            "page": 2,
            "site": "ebay",
        }

    box._browse = fake_browse

    asyncio.run(box.search("ebay", "query"))
    asyncio.run(box.next_page("ebay"))

    assert box.search_pending_scrape is False


def test_zero_result_scrape_also_releases_the_pending_search():
    session = store.create("generic requirement", "ebay")
    box = ToolBox(session)

    async def fake_browse(tool, _args):
        if tool == "search":
            return {"status": "ok", "page": 1, "site": "ebay"}
        return {
            "status": "ok",
            "items": [],
            "page": 1,
            "site": "ebay",
            "no_results": True,
        }

    box._browse = fake_browse

    asyncio.run(box.search("ebay", "empty query"))
    asyncio.run(box.scrape_listing("ebay"))

    assert box.search_pending_scrape is False
