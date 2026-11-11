"""eBay detail results must reach the inventory and the fit judge."""
from __future__ import annotations

import asyncio

from app.fit_judge import _listing
from app.store import store
from app.tools import ToolBox


def _ebay_product() -> dict:
    return {
        "product_id": "p1",
        "source": "ebay",
        "source_product_id": "111222333444",
        "title": "Lenovo ThinkPad T14",
        "price": 600.0,
        "currency": "USD",
        "price_cny": 4320.0,
        "url": "https://www.ebay.com/itm/111222333444",
        "detail_fetched": False,
        "attributes": {},
    }


def test_fetch_detail_keeps_range_and_metadata(monkeypatch):
    session = store.create("需要笔记本", "ebay")
    session.products["p1"] = _ebay_product()
    box = ToolBox(session)

    async def fake_browse(tool, args):
        assert tool == "fetch_detail"
        assert args["source_product_id"] == "111222333444"
        return {
            "status": "ok",
            "title": "Lenovo ThinkPad T14 Gen 4 32GB",
            "price": 620,
            "price_max": 700,
            "price_is_range": True,
            "currency": "USD",
            "attributes": {"RAM Size": "32 GB"},
            "condition": "Pre-Owned",
            "seller": {"name": "refurb-dealer", "feedback_percent": 99.2},
            "shipping": "Free International Shipping",
            "shipping_cost": 0,
            "returns": "30 day returns",
            "location": "Shenzhen, China",
            "availability": "available",
            "listing_type": "buy_it_now",
            "best_offer": True,
            "has_variations": True,
            "variations": ["RAM", "Storage"],
            "highlights": ["14-inch display", "32GB RAM"],
            "images": ["https://i.ebayimg.com/images/g/abc/s-l1600.jpg"],
            "item_number": "111222333444",
        }

    monkeypatch.setattr(box, "_browse", fake_browse)
    result = asyncio.run(box.fetch_detail("p1"))

    assert result["status"] == "ok"
    product = session.products["p1"]
    assert product["detail_fetched"] is True
    assert product["price"] == 620
    assert product["price_cny"] == 4464
    assert product["price_max_cny"] == 5040
    assert product["price_is_range"] is True
    assert product["attributes"]["RAM Size"] == "32 GB"
    assert product["condition"] == "Pre-Owned"
    assert product["seller"]["feedback_percent"] == 99.2
    assert product["shipping"] == "Free International Shipping"
    assert product["returns"] == "30 day returns"
    assert product["location"] == "Shenzhen, China"
    assert product["availability"] == "available"
    assert product["listing_type"] == "buy_it_now"
    assert product["best_offer"] is True
    assert product["has_variations"] is True
    assert product["variations"] == ["RAM", "Storage"]
    assert product["highlights"] == ["14-inch display", "32GB RAM"]
    assert product["image_url"] == (
        "https://i.ebayimg.com/images/g/abc/s-l1600.jpg"
    )


def test_fit_judge_payload_includes_detail_evidence():
    record = _listing(
        {
            "product_id": "p1",
            "title": "Lenovo ThinkPad T14",
            "category": "笔记本",
            "attributes": {},
            "detail": {
                "title": "Lenovo ThinkPad T14 Gen 4",
                "attributes": {"RAM Size": "32 GB"},
                "condition": "Pre-Owned",
                "highlights": ["14-inch display", "32GB RAM"],
            },
        }
    )

    assert record["detail_condition"] == "Pre-Owned"
    assert record["detail_highlights"] == ["14-inch display", "32GB RAM"]
    assert record["detail_attributes"]["RAM Size"] == "32 GB"
