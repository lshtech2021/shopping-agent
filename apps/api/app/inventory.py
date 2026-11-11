"""Product inventory: upsert/index, summaries, and diverse sampling."""
from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from app.config import settings
from app.constraints import check_product, must_have_match_count


def select_diverse_products(
    products: list[dict[str, Any]],
    requirements: dict[str, Any],
    *,
    sites: list[str],
    limit: int,
    require_detail: bool = False,
) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    eligible = [
        product
        for product in products
        if check_product(
            product,
            requirements,
            sites=sites,
            require_detail=require_detail,
        ).ok
    ]
    ordered = sorted(
        eligible,
        key=lambda product: _rank_key(product, requirements),
    )

    budget = _budget(requirements)
    if budget is None:
        return ordered[:limit]

    bands: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for product in ordered:
        price = float(product.get("price_cny") or 0)
        index = min(3, max(0, int(price / budget * 4)))
        bands[index].append(product)

    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index in sorted(bands):
        if bands[index]:
            product = bands[index].pop(0)
            selected.append(product)
            seen.add(str(product.get("product_id")))
        if len(selected) >= limit:
            return selected

    for product in ordered:
        product_id = str(product.get("product_id"))
        if product_id in seen:
            continue
        selected.append(product)
        seen.add(product_id)
        if len(selected) >= limit:
            break
    return selected


def _rank_key(
    product: dict[str, Any], requirements: dict[str, Any]
) -> tuple:
    return (
        not bool(product.get("detail_fetched")),
        -must_have_match_count(product, requirements),
        -(int(product.get("sales") or 0)),
        float(product.get("price_cny") or 0),
        str(product.get("product_id") or ""),
    )


def _budget(requirements: dict[str, Any]) -> float | None:
    if requirements.get("budget_unlimited"):
        return None
    value = requirements.get("budget_cny")
    if value is None:
        return None
    try:
        budget = float(value)
    except (TypeError, ValueError):
        return None
    return budget if budget > 0 else None


def inventory_summary(products: list[dict[str, Any]]) -> str:
    if not products:
        return "库存摘要：无已抓商品"
    by_site: dict[str, int] = {}
    for product in products:
        by_site[product.get("source") or "?"] = (
            by_site.get(product.get("source") or "?", 0) + 1
        )
    priced = [
        product["price_cny"]
        for product in products
        if product.get("price_cny") is not None
    ]
    price_part = (
        f"价区¥{min(priced):.0f}-¥{max(priced):.0f}" if priced else "价区未知"
    )
    site_part = "；".join(f"{key}={value}" for key, value in sorted(by_site.items()))
    return (
        f"库存摘要：商品数={len(products)}；总计={len(products)}；"
        f"{site_part}；{price_part}"
    )


def to_cny(price: Any, currency: str | None) -> float | None:
    if price is None:
        return None
    try:
        value = float(price)
    except (TypeError, ValueError):
        return None
    code = (currency or "CNY").upper()
    rates = {
        str(key).upper(): float(rate)
        for key, rate in (settings.fx_rates or {}).items()
    }
    rate = rates.get(code)
    if rate is None or rate <= 0:
        return None
    return round(value * rate, 2)


def build_product_index(
    products: dict[str, dict[str, Any]],
) -> dict[tuple[str, str], str]:
    index: dict[tuple[str, str], str] = {}
    for product_id, product in products.items():
        source = str(product.get("source") or "")
        source_id = str(product.get("source_product_id") or "").strip()
        if source and source_id:
            index[(source, source_id)] = str(product_id)
    return index


def upsert_listing(
    products: dict[str, dict[str, Any]],
    index: dict[tuple[str, str], str],
    *,
    site: str,
    item: dict[str, Any],
) -> bool:
    source_id = str(item.get("source_product_id") or "").strip()
    if not source_id:
        return False
    key = index.get((site, source_id))
    if key is None or key not in products:
        key = uuid.uuid4().hex[:12]
    price = item.get("price")
    currency = item.get("currency") or ("CNY" if site == "jd" else "USD")
    existing = products.get(key, {})
    detail_owned = bool(existing.get("detail_fetched"))
    price_is_range = bool(item.get("price_is_range"))
    price_max = item.get("price_max") if price_is_range else None
    listing_fields = {
        "title": item.get("title") or source_id,
        "price": price,
        "currency": currency,
        "price_cny": to_cny(price, currency),
        "price_max_cny": to_cny(price_max, currency),
        "price_is_range": price_is_range,
    }
    if detail_owned:
        listing_fields = {
            "title": existing.get("title") or source_id,
            "price": existing.get("price"),
            "currency": existing.get("currency") or currency,
            "price_cny": existing.get("price_cny"),
            "price_max_cny": existing.get("price_max_cny"),
            "price_is_range": existing.get("price_is_range", False),
        }
    def _pick(name: str, default: Any = None) -> Any:
        value = item.get(name)
        if value is None:
            value = existing.get(name, default)
        return value

    observation_fields = {
        "image_url": _pick("image_url"),
        "images": _pick("images") or [],
        "listing_type": _pick("listing_type"),
        "availability": _pick("availability"),
        "location": _pick("location"),
        "seller": _pick("seller"),
        "watchers": _pick("watchers"),
        "sold": _pick("sold"),
        "quantity_available": _pick("quantity_available"),
        "shipping_cost": _pick("shipping_cost"),
        "returns": _pick("returns"),
        "best_offer": _pick("best_offer"),
        "bids": _pick("bids"),
        "has_variations": _pick("has_variations"),
        "variations": _pick("variations") or [],
        "highlights": _pick("highlights") or [],
        "item_number": _pick("item_number"),
    }
    products[key] = {
        **existing,
        "product_id": key,
        "source": site,
        "source_product_id": source_id,
        "url": _pick("url"),
        "shop_name": _pick("shop_name"),
        "sales": _pick("sales"),
        "rating": _pick("rating"),
        "sponsored": _pick("sponsored", False),
        "shipping": _pick("shipping"),
        "condition": _pick("condition"),
        "badges": _pick("badges") or [],
        "category": _pick("category"),
        "detail_fetched": existing.get("detail_fetched", False),
        "attributes": existing.get("attributes") or {},
        **observation_fields,
        **listing_fields,
    }
    index[(site, source_id)] = key
    return True
