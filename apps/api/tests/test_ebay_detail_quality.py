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
    assert product["price_max"] == 700
    assert product["currency"] == "USD"
    assert product["price_is_range"] is True
    assert product["landed_cost"] == 700
    assert product["cost_complete"] is True
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


def test_detail_currency_mismatch_keeps_listing_price(monkeypatch):
    session = store.create("需要笔记本", "ebay")
    session.settings["currency"] = "CNY"
    session.products["p1"] = {
        **_ebay_product(),
        "price": 8.34,
        "currency": "CNY",
    }
    box = ToolBox(session)

    async def fake_browse(tool, args):
        assert tool == "fetch_detail"
        return {
            "status": "ok",
            "title": "Dell Precision 3490",
            "price": 1.25,
            "currency": "USD",
            "attributes": {"RAM Size": "16 GB"},
            "condition": "Pre-Owned",
            "shipping_cost": 77.65,
        }

    monkeypatch.setattr(box, "_browse", fake_browse)
    result = asyncio.run(box.fetch_detail("p1"))

    product = session.products["p1"]
    assert result["status"] == "ok"
    assert result["currency_mismatch"] is True
    assert product["price"] == 8.34
    assert product["currency"] == "CNY"
    assert product["detail_currency"] == "USD"
    assert product["detail_currency_mismatch"] is True
    assert product.get("shipping_cost") is None
    assert product["cost_complete"] is False
    assert product["landed_cost"] is None
    assert product["attributes"]["RAM Size"] == "16 GB"


def test_detail_approx_price_in_session_currency_is_accepted(monkeypatch):
    session = store.create("需要笔记本", "ebay")
    session.settings["currency"] = "CNY"
    session.products["p1"] = {
        **_ebay_product(),
        "price": 2228.97,
        "currency": "CNY",
    }
    box = ToolBox(session)

    async def fake_browse(tool, args):
        assert tool == "fetch_detail"
        return {
            "status": "ok",
            "title": "ThinkPad T14",
            "price": 299,
            "currency": "EUR",
            "approx_price": 2228.97,
            "approx_currency": "CNY",
            "shipping_cost": 20,
            "attributes": {"RAM Size": "16 GB"},
        }

    monkeypatch.setattr(box, "_browse", fake_browse)
    result = asyncio.run(box.fetch_detail("p1"))

    product = session.products["p1"]
    assert result["status"] == "ok"
    assert "currency_mismatch" not in result
    assert product["price"] == 2228.97
    assert product["currency"] == "CNY"
    assert product["detail_currency_mismatch"] is False
    assert product["detail_price_source"] == "approx"
    assert product["detail_primary_currency"] == "EUR"
    assert product["cost_complete"] is False
    assert product["landed_cost"] is None


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
