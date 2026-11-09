"""Inventory module helpers live outside ToolBox."""
from __future__ import annotations

from app.inventory import (
    build_product_index,
    inventory_summary,
    to_cny,
    upsert_listing,
)


def test_inventory_summary_counts_sites_and_price_band():
    text = inventory_summary(
        [
            {"source": "jd", "price_cny": 100},
            {"source": "jd", "price_cny": 300},
            {"source": "amazon", "price_cny": 200},
        ]
    )
    assert "商品数=3" in text
    assert "amazon=1" in text
    assert "jd=2" in text
    assert "价区¥100-¥300" in text
    assert "笔记本" not in text


def test_upsert_listing_reuses_index_without_toolbox():
    products: dict = {}
    index: dict = {}
    assert upsert_listing(
        products,
        index,
        site="jd",
        item={
            "source_product_id": "sku-1",
            "title": "A",
            "price": 100,
            "currency": "CNY",
        },
    )
    first_id = next(iter(products))
    assert index[("jd", "sku-1")] == first_id
    assert upsert_listing(
        products,
        index,
        site="jd",
        item={
            "source_product_id": "sku-1",
            "title": "A2",
            "price": 120,
            "currency": "CNY",
        },
    )
    assert list(products) == [first_id]
    assert products[first_id]["title"] == "A2"
    assert products[first_id]["price_cny"] == 120.0


def test_build_product_index_and_to_cny():
    products = {
        "p1": {"source": "jd", "source_product_id": "1"},
        "orphan": {"source": "", "source_product_id": "x"},
    }
    assert build_product_index(products) == {("jd", "1"): "p1"}
    assert to_cny(10, "CNY") == 10.0
    assert to_cny(None, "CNY") is None
