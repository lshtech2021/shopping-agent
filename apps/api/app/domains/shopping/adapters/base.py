"""Backend site-pack contract and shared adapter helpers."""
from __future__ import annotations

from typing import Any, Literal, Protocol


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
        buy_it_now_only: bool | None = None,
    ) -> dict[str, Any]: ...


# The item price cap leaves room for shipping and import costs. The exact
# landed cost is checked again after fetch_detail.
SEARCH_PRICE_BUFFER_RATIO = 0.85


def budget_search_filters(
    requirements: dict[str, Any],
    max_price: float | None,
    *,
    buy_it_now_only: bool | None = None,
) -> dict[str, Any]:
    budget = None
    if not requirements.get("budget_unlimited"):
        budget = requirements.get("budget")
    caps: list[float] = []
    if max_price is not None:
        caps.append(float(max_price))
    if budget is not None:
        caps.append(float(budget) * SEARCH_PRICE_BUFFER_RATIO)
    cap = min(caps) if caps else None
    filters: dict[str, Any] = {}
    if cap is not None:
        filters["maxPrice"] = round(cap, 2)
    if buy_it_now_only:
        filters["buyItNowOnly"] = True
    return filters
