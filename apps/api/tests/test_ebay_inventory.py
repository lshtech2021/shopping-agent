"""eBay listing observations must survive inventory upserts and constraints."""
from __future__ import annotations

from app.constraints import check_product
from app.inventory import build_product_index, upsert_listing
from app.store import store
from app.tools import ToolBox


def _ebay_listing() -> dict:
    return {
        "source_product_id": "111222333444",
        "url": "https://www.ebay.com/itm/111222333444?var=555",
        "title": "Lenovo ThinkPad T14",
        "price": 620,
        "price_max": 700,
        "price_is_range": True,
        "currency": "USD",
        "image_url": "https://i.ebayimg.com/images/g/abc/s-l1600.jpg",
        "shop_name": "refurb-dealer",
        "sales": 12,
        "watchers": 4,
        "rating": 4.6,
        "sponsored": False,
        "shipping": "Free delivery",
        "shipping_cost": 0,
        "shipping_currency": None,
        "condition": "Pre-Owned",
        "listing_type": "buy_it_now",
        "availability": "available",
        "location": "Shenzhen, China",
        "seller": {"name": "refurb-dealer", "feedback_percent": 99.2},
        "highlights": ["14-inch display", "32GB RAM"],
        "has_variations": True,
        "variations": ["RAM", "Storage"],
        "images": ["https://i.ebayimg.com/images/g/abc/s-l1600.jpg"],
        "item_number": "111222333444",
        "category": "Laptops",
    }


def test_upsert_preserves_ebay_listing_metadata():
    products: dict = {}
    index = build_product_index(products)

    assert upsert_listing(
        products, index, site="ebay", item=_ebay_listing()
    )

    product = next(iter(products.values()))
    assert product["image_url"].endswith("s-l1600.jpg")
    assert product["condition"] == "Pre-Owned"
    assert product["shipping"] == "Free delivery"
    assert product["shipping_cost"] == 0
    assert product["shipping_currency"] is None
    assert product["listing_type"] == "buy_it_now"
    assert product["availability"] == "available"
    assert product["location"] == "Shenzhen, China"
    assert product["seller"]["feedback_percent"] == 99.2
    assert product["watchers"] == 4
    assert product["sales"] == 12
    assert product["rating"] == 4.6
    assert product["highlights"] == ["14-inch display", "32GB RAM"]
    assert product["has_variations"] is True
    assert product["variations"] == ["RAM", "Storage"]
    assert product["price"] == 620
    assert product["price_max"] == 700
    assert product["currency"] == "USD"
    assert product["price_is_range"] is True


def test_rescrape_keeps_detail_owned_fields():
    products: dict = {}
    index = build_product_index(products)
    assert upsert_listing(
        products, index, site="ebay", item=_ebay_listing()
    )
    product_id = next(iter(products))
    products[product_id].update(
        {
            "detail_fetched": True,
            "image_url": "https://i.ebayimg.com/images/g/detail/s-l1600.jpg",
            "availability": "unavailable",
            "highlights": ["Verified by detail page"],
            "shipping_cost": 50,
            "shipping_currency": "USD",
        }
    )

    # A later listing scrape has no detail-only fields and must not erase them.
    rescrape = {
        "source_product_id": "111222333444",
        "url": "https://www.ebay.com/itm/111222333444",
        "title": "Lenovo ThinkPad T14",
        "price": 640,
        "currency": "USD",
        "shipping": "USD 20.00 shipping",
        "shipping_cost": 20,
        "shipping_currency": "USD",
    }
    assert upsert_listing(products, index, site="ebay", item=rescrape)

    product = products[product_id]
    assert product["detail_fetched"] is True
    assert product["image_url"].endswith("detail/s-l1600.jpg")
    assert product["availability"] == "unavailable"
    assert product["highlights"] == ["Verified by detail page"]
    # Detail-owned price and shipping are authoritative after fetch_detail.
    assert product["price"] == 620
    assert product["shipping_cost"] == 50
    assert product["shipping_currency"] == "USD"


def test_unavailable_product_is_ineligible():
    result = check_product(
        {
            "source": "ebay",
            "price": 100.0,
            "currency": "USD",
            "availability": "unavailable",
        },
        {},
        settings={"site": "ebay", "currency": "USD"},
        sites=["ebay"],
    )
    assert result.ok is False
    assert "availability" in result.violations


def test_query_inventory_exposes_ebay_observations():
    session = store.create("需要笔记本", "ebay")
    session.products["p1"] = {
        "product_id": "p1",
        "source": "ebay",
        "source_product_id": "111222333444",
        "title": "Lenovo ThinkPad T14",
        "price": 620.0,
        "currency": "USD",
        "url": "https://www.ebay.com/itm/111222333444",
        "detail_fetched": True,
        "attributes": {},
        "image_url": "https://i.ebayimg.com/images/g/abc/s-l1600.jpg",
        "availability": "available",
        "listing_type": "buy_it_now",
        "seller": {"name": "refurb-dealer", "feedback_percent": 99.2},
        "watchers": 4,
        "sold": 25,
        "location": "Shenzhen, China",
        "shipping_cost": 0,
        "has_variations": True,
    }

    item = ToolBox(session).query_inventory(limit=10)["items"][0]
    assert item["image_url"].endswith("s-l1600.jpg")
    assert item["availability"] == "available"
    assert item["listing_type"] == "buy_it_now"
    assert item["seller"]["feedback_percent"] == 99.2
    assert item["watchers"] == 4
    assert item["sold"] == 25
    assert item["location"] == "Shenzhen, China"
    assert item["shipping_cost"] == 0
    assert item["has_variations"] is True


def test_shipping_currency_is_cleared_without_an_amount():
    products: dict = {}
    index = build_product_index(products)
    upsert_listing(
        products,
        index,
        site="ebay",
        item={
            "source_product_id": "111",
            "title": "Laptop",
            "price": 1000,
            "currency": "CNY",
            "shipping": "Shipping not specified",
            "shipping_cost": None,
            "shipping_currency": "CNY",
        },
    )
    product = next(iter(products.values()))
    assert product["shipping_cost"] is None
    assert product["shipping_currency"] is None
