
import asyncio

from app.store import store
from app.tools import ToolBox


def test_failed_fetch_detail_does_not_consume_quota(monkeypatch):
    session = store.create("笔记本", ["jd"])
    session.products["p1"] = {
        "product_id": "p1",
        "source": "jd",
        "source_product_id": "1001",
        "title": "测试本",
        "url": "https://item.jd.com/1001.html",
        "detail_fetched": False,
        "attributes": {},
    }
    box = ToolBox(session)

    async def fake_browse(tool, args):
        return {"status": "parse_error", "error": "detail_not_found"}

    monkeypatch.setattr(box, "_browse", fake_browse)
    result = asyncio.run(box.fetch_detail("p1"))
    assert result["status"] == "parse_error"
    assert session.quotas.get("jd", {}).get("fetch_detail", 0) == 0
