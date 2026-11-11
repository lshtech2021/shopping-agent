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
    ) -> dict[str, Any]: ...

def budget_search_filters(
    requirements: dict[str, Any], max_price: float | None
) -> dict[str, Any]:
    cap = max_price
    if cap is None and not requirements.get("budget_unlimited"):
        cap = requirements.get("budget")
    return {"maxPrice": cap} if cap is not None else {}
