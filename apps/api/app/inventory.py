"""Deterministic inventory sampling: price bands + relevance, never cheapest-first."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

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
