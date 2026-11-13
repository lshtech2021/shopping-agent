"""eBay backend site pack."""
from __future__ import annotations

from typing import Any, Literal

from app.currency import normalize_currency
from app.domains.shopping.adapters.base import budget_search_filters


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _shipping_caps_configured(settings: dict[str, Any]) -> bool:
    return (
        _number(settings.get("max_shipping_ratio")) is not None
        or _number(settings.get("max_shipping_cost")) is not None
    )


def _shipping_cap_exceeded(product: dict[str, Any], settings: dict[str, Any]) -> bool:
    ratio = _number(settings.get("max_shipping_ratio"))
    absolute = _number(settings.get("max_shipping_cost"))
    if ratio is None and absolute is None:
        return False
    shipping = _number(product.get("shipping_cost"))
    if shipping is None:
        return False
    price = _number(
        product.get("price_max")
        if product.get("price_is_range")
        else product.get("price")
    )
    product_currency = normalize_currency(product.get("currency"))
    shipping_currency = normalize_currency(product.get("shipping_currency"))
    session_currency = normalize_currency(settings.get("currency"))

    if (
        ratio is not None
        and price is not None
        and price > 0
        and shipping_currency in (None, product_currency)
        and shipping > price * ratio
    ):
        return True
    if (
        absolute is not None
        and session_currency is not None
        and product_currency == session_currency
        and shipping_currency in (None, product_currency)
        and shipping > absolute
    ):
        return True
    return False


class EbayAdapter:
    id: Literal["ebay"] = "ebay"

    def system_prompt_extra(
        self, settings: dict[str, Any], requirements: dict[str, Any]
    ) -> str:
        currency = str(settings.get("currency") or "the session currency")
        return (
            "## eBay site guidance\n"
            "- eBay ANDs every keyword. Use 1-3 short keywords in the site's "
            "language, never a use-case sentence.\n"
            "- Express a user-stated category constraint with `category_id`; "
            "prefix an exclusion term with `-` only when the user asked for "
            "that exclusion.\n"
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
            buffer_ratio=settings.get("search_price_buffer"),
        )

    def eligibility_extra(
        self,
        product: dict[str, Any],
        settings: dict[str, Any],
        requirements: dict[str, Any],
    ) -> list[str]:
        violations: list[str] = []
        shipping_caps = _shipping_caps_configured(settings)
        if _shipping_cap_exceeded(product, settings):
            violations.append("shipping")

        budget = requirements.get("budget")
        budget_min = requirements.get("budget_min")
        has_upper = (
            not requirements.get("budget_unlimited") and budget is not None
        )
        has_lower = budget_min is not None
        has_budget = has_upper or has_lower
        if has_upper and product.get("listing_type") == "auction":
            violations.append("auction")
        if has_budget and product.get("cost_complete") is False:
            violations.append("cost_incomplete")
        if (
            shipping_caps
            and not has_budget
            and product.get("cost_complete") is False
        ):
            violations.append("cost_incomplete")
        return violations


adapter = EbayAdapter()
