"""Backend site-pack contract and shared adapter helpers."""
from __future__ import annotations

from typing import Any, Literal, Protocol

from app.config import settings as app_settings


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

    def eligibility_extra(
        self,
        product: dict[str, Any],
        settings: dict[str, Any],
        requirements: dict[str, Any],
    ) -> list[str]: ...


def budget_search_filters(
    requirements: dict[str, Any],
    max_price: float | None,
    *,
    buy_it_now_only: bool | None = None,
    buffer_ratio: float | None = None,
) -> dict[str, Any]:
    """Build search price filters; buffer leaves room for shipping/tax.

    The exact landed cost is checked again after fetch_detail.
    """
    budget = None
    if not requirements.get("budget_unlimited"):
        budget = requirements.get("budget")
    default_ratio = float(app_settings.search_price_buffer)
    ratio = default_ratio
    if buffer_ratio is not None:
        try:
            ratio = float(buffer_ratio)
        except (TypeError, ValueError):
            ratio = default_ratio
    caps: list[float] = []
    if max_price is not None:
        caps.append(float(max_price))
    if budget is not None:
        caps.append(float(budget) * ratio)
    cap = min(caps) if caps else None
    filters: dict[str, Any] = {}
    if cap is not None:
        filters["maxPrice"] = round(cap, 2)
    if buy_it_now_only:
        filters["buyItNowOnly"] = True
    return filters
