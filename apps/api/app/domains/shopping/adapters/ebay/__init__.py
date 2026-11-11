"""eBay backend site pack."""
from __future__ import annotations

from typing import Any, Literal

from app.domains.shopping.adapters.base import budget_search_filters


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
        buy_it_now_only: bool | None = None,
    ) -> dict[str, Any]:
        return budget_search_filters(
            requirements,
            max_price,
            buy_it_now_only=buy_it_now_only,
        )

adapter = EbayAdapter()
