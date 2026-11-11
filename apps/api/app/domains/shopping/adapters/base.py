"""Backend site-pack contract and shared adapter helpers."""
from __future__ import annotations

from typing import Any, Literal, Protocol, TypedDict


class SearchPlan(TypedDict, total=False):
    maxPrice: float


class SiteAdapter(Protocol):
    id: Literal["jd", "ebay"]

    def system_prompt_extra(
        self, settings: dict[str, Any], requirements: dict[str, Any]
    ) -> str: ...

    def build_search_filters(
        self,
        *,
        query: str,
        requirements: dict[str, Any],
        settings: dict[str, Any],
        max_price: float | None,
    ) -> dict[str, Any]: ...

    def eligible_for_budget(
        self,
        product: dict[str, Any],
        settings: dict[str, Any],
        budget: float | None,
    ) -> bool: ...

    def prefilter(
        self,
        products: list[dict[str, Any]],
        requirements: dict[str, Any],
    ) -> list[dict[str, Any]]: ...


def budget_search_filters(
    requirements: dict[str, Any], max_price: float | None
) -> dict[str, Any]:
    cap = max_price
    if cap is None and not requirements.get("budget_unlimited"):
        cap = requirements.get("budget")
    return {"maxPrice": cap} if cap is not None else {}


def eligible_for_budget(
    product: dict[str, Any],
    settings: dict[str, Any],
    budget: float | None,
) -> bool:
    if budget is None:
        return True
    product_currency = str(product.get("currency") or "").upper()
    session_currency = str(settings.get("currency") or "").upper()
    if not product_currency or product_currency != session_currency:
        return False
    price = (
        product.get("price_max")
        if product.get("price_is_range")
        else product.get("price")
    )
    return price is not None and float(price) <= float(budget)
