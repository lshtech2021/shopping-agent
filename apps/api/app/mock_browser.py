"""Deterministic mock catalog for Phase 1 closed-loop without live sites."""
from __future__ import annotations

from typing import Any


MOCK_CATALOG: dict[str, list[dict[str, Any]]] = {
    "jd": [
        {
            "source_product_id": "jd-1001",
            "title": "ThinkPad T14 锐龙7 32G 1T SSD 开发本",
            "price": 4899.0,
            "currency": "CNY",
            "url": "https://item.jd.com/1001.html",
            "shop_name": "联想京东自营",
            "attributes": {"cpu": "R7-7840U", "ram": "32GB", "ssd": "1TB"},
        },
        {
            "source_product_id": "jd-1002",
            "title": "小新 Pro14 16G 512G 轻薄本",
            "price": 4299.0,
            "currency": "CNY",
            "url": "https://item.jd.com/1002.html",
            "shop_name": "联想京东自营",
            "attributes": {"cpu": "i5-13500H", "ram": "16GB", "ssd": "512GB"},
        },
        {
            "source_product_id": "jd-1003",
            "title": "笔记本电脑贴纸 保护膜 配件",
            "price": 29.0,
            "currency": "CNY",
            "url": "https://item.jd.com/1003.html",
            "shop_name": "配件专营",
            "attributes": {"type": "accessory"},
        },
    ],
    "ebay": [
        {
            "source_product_id": "ebay-2001",
            "title": "Lenovo ThinkPad T14 Gen 4 32GB 1TB SSD",
            "price": 620.0,
            "currency": "USD",
            "url": "https://www.ebay.com/itm/2001",
            "shop_name": "refurb-dealer",
            "attributes": {"cpu": "i7-1355U", "ram": "32GB", "ssd": "1TB"},
        },
        {
            "source_product_id": "ebay-2002",
            "title": "Used Dell Latitude 14 laptop 16GB",
            "price": 380.0,
            "currency": "USD",
            "url": "https://www.ebay.com/itm/2002",
            "shop_name": "us-seller",
            "attributes": {"cpu": "i5-1135G7", "ram": "16GB", "ssd": "512GB"},
        },
    ],
}


def mock_search(site: str, query: str) -> dict[str, Any]:
    site = (site or "").lower()
    if site not in MOCK_CATALOG:
        return {"status": "blocked", "error": f"unknown site {site}"}
    if query.strip().lower() == "rate_limit_me":
        return {"status": "rate_limited", "site": site, "query": query}
    return {
        "status": "ok",
        "site": site,
        "query": query,
        "url": f"mock://{site}/search?q={query}",
        "mode": "mock",
    }


def mock_scrape_listing(site: str, page: int = 1) -> dict[str, Any]:
    site = (site or "").lower()
    items = MOCK_CATALOG.get(site, [])
    if page > 1:
        return {"status": "ok", "items": [], "page": page, "no_more": True, "mode": "mock"}
    return {
        "status": "ok",
        "items": [
            {
                "source_product_id": item["source_product_id"],
                "title": item["title"],
                "price": item["price"],
                "currency": item["currency"],
                "url": item["url"],
                "shop_name": item["shop_name"],
            }
            for item in items
        ],
        "page": page,
        "mode": "mock",
    }


def mock_fetch_detail(site: str, source_product_id: str) -> dict[str, Any]:
    for item in MOCK_CATALOG.get((site or "").lower(), []):
        if item["source_product_id"] == source_product_id:
            return {
                "status": "ok",
                "source_product_id": source_product_id,
                "title": item["title"],
                "price": item["price"],
                "currency": item["currency"],
                "url": item["url"],
                "attributes": item["attributes"],
                "mode": "mock",
            }
    return {"status": "parse_error", "error": "product not found in mock catalog"}
