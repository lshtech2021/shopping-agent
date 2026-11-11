"""Navigation jobs must produce distinct ids after a new search."""
from __future__ import annotations

import asyncio

from app.browser.action_broker import ActionBroker
from app.store import store
from app.tools import ToolBox


def test_scrape_action_args_change_after_a_new_search(monkeypatch):
    session = store.create("需要笔记本", ["ebay"])
    box = ToolBox(session)
    calls: list[tuple[str, dict]] = []

    async def fake_browse(tool, args):
        calls.append((tool, dict(args)))
        if tool == "search":
            url = (
                "https://www.ebay.com/sch/i.html?_nkw="
                + str(args.get("query") or "").replace(" ", "+")
            )
            return {
                "status": "ok",
                "site": "ebay",
                "page": 1,
                "url": url,
                "document_url": url,
            }
        return {"status": "ok", "items": [], "page": args.get("page", 1)}

    monkeypatch.setattr(box, "_browse", fake_browse)

    asyncio.run(box.search("ebay", "laptop"))
    asyncio.run(box.scrape_listing("ebay", 1))
    asyncio.run(box.search("ebay", "thinkpad laptop"))
    asyncio.run(box.scrape_listing("ebay", 1))

    scrape_args = [args for tool, args in calls if tool == "scrape_listing"]
    assert len(scrape_args) == 2
    assert scrape_args[0] != scrape_args[1]
    assert scrape_args[0]["search_seq"] != scrape_args[1]["search_seq"]
    assert scrape_args[0]["search_url"] != scrape_args[1]["search_url"]

    action_ids = {
        ActionBroker.deterministic_id(
            session.id, "scrape_listing", args, turn=session.turn
        )
        for args in scrape_args
    }
    assert len(action_ids) == 2
