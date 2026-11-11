"""eBay backend site pack."""
from __future__ import annotations

from typing import Any, Literal

from app.domains.shopping.adapters.base import (
    budget_search_filters,
    eligible_for_budget,
)


class EbayAdapter:
    id: Literal["ebay"] = "ebay"

    def system_prompt_extra(
        self, settings: dict[str, Any], requirements: dict[str, Any]
    ) -> str:
        currency = str(settings.get("currency") or "the session currency")
        return (
            "## eBay site guidance\n"
            "- eBay ANDs every keyword. Use 1-3 short keywords (for example "
            "`laptop`, `thinkpad laptop`, or `laptop 16GB`), never a use-case "
            "sentence.\n"
            "- Express category constraints with `category_id` and prefix "
            "exclusion terms with `-`.\n"
            f"- The budget and search price cap are in {currency}."
        )

    def build_search_filters(
        self,
        *,
        query: str,
        requirements: dict[str, Any],
        settings: dict[str, Any],
        max_price: float | None,
    ) -> dict[str, Any]:
        return budget_search_filters(requirements, max_price)

    def eligible_for_budget(
        self,
        product: dict[str, Any],
        settings: dict[str, Any],
        budget: float | None,
    ) -> bool:
        return eligible_for_budget(product, settings, budget)

    def prefilter(
        self,
        products: list[dict[str, Any]],
        requirements: dict[str, Any],
    ) -> list[dict[str, Any]]:
        return products


adapter = EbayAdapter()
