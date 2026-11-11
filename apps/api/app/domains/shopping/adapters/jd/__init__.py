"""JD backend site pack."""
from __future__ import annotations

from typing import Any, Literal

from app.domains.shopping.adapters.base import eligible_for_budget


class JDAdapter:
    id: Literal["jd"] = "jd"

    def system_prompt_extra(
        self, settings: dict[str, Any], requirements: dict[str, Any]
    ) -> str:
        currency = str(settings.get("currency") or "the session currency")
        return (
            "## JD site guidance\n"
            "- Use concise Chinese product keywords for JD searches.\n"
            f"- The budget and search price cap are in {currency}.\n"
            "- Treat a displayed price range as a range until a detail page "
            "confirms the selected variation."
        )

    def build_search_filters(
        self,
        *,
        query: str,
        requirements: dict[str, Any],
        settings: dict[str, Any],
        max_price: float | None,
    ) -> dict[str, Any]:
        return {}

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


adapter = JDAdapter()
