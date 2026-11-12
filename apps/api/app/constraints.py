"""Product eligibility shared by inventory, recommendation and the critic.

Structured checks (site, budget, detail) are exact and live here. Whether a
listing *fits* the requested category and must-have items is a judgment made by
an LLM (`app.fit_judge`); its verdict is stored on the product under
``product["fit"]`` and only read here. A product whose verdict is missing or
stale for the current requirements is ineligible (`unjudged`) until judged.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.currency import normalize_currency

FIT_KEY = "fit"
_PARTS_CONDITIONS = {
    "for parts",
    "for parts or not working",
    "parts only",
    "not working",
}


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _shipping_caps_configured(settings: dict[str, Any]) -> bool:
    # JD prices normally include delivery and its detail parser has no
    # shipping amount, so caps only apply to eBay evidence.
    if settings.get("site") != "ebay":
        return False
    return (
        _number(settings.get("max_shipping_ratio")) is not None
        or _number(settings.get("max_shipping_cost")) is not None
    )


def _shipping_cap_exceeded(product: dict[str, Any], settings: dict[str, Any]) -> bool:
    """Apply both caps independently; either one excludes the product.

    An absolute cap is a hard veto even when the percentage check passes.
    """
    if settings.get("site") != "ebay":
        return False
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


@dataclass(frozen=True)
class EligibilityResult:
    ok: bool
    violations: list[str]


def needs_fit(requirements: dict[str, Any] | None) -> bool:
    requirements = requirements or {}
    return bool(requirements.get("category") or requirements.get("must_have"))


def fit_signature(
    product: dict[str, Any], requirements: dict[str, Any] | None
) -> str:
    """Identity of what a verdict was based on; any change makes it stale."""
    requirements = requirements or {}
    return json.dumps(
        {
            "category": requirements.get("category"),
            "must_have": sorted(
                str(item) for item in requirements.get("must_have") or []
            ),
            "detail": bool(product.get("detail_fetched")),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def needs_judging(
    product: dict[str, Any], requirements: dict[str, Any] | None
) -> bool:
    if not needs_fit(requirements):
        return False
    fit = product.get(FIT_KEY)
    return not isinstance(fit, dict) or fit.get("sig") != fit_signature(
        product, requirements
    )


def _current_fit(
    product: dict[str, Any], requirements: dict[str, Any]
) -> dict[str, Any] | None:
    fit = product.get(FIT_KEY)
    if isinstance(fit, dict) and fit.get("sig") == fit_signature(
        product, requirements
    ):
        return fit
    return None


def check_eligibility(
    product: dict[str, Any],
    requirements: dict[str, Any],
    *,
    settings: dict[str, Any],
    sites: list[str],
    require_detail: bool = False,
    include_fit: bool = True,
) -> EligibilityResult:
    requirements = requirements or {}
    violations: list[str] = []

    if product.get("source") not in sites:
        violations.append("site")

    if product.get("availability") == "unavailable":
        violations.append("availability")

    condition = str(product.get("condition") or "").strip().lower()
    if condition in _PARTS_CONDITIONS:
        violations.append("parts")

    shipping_caps_configured = _shipping_caps_configured(settings)
    if _shipping_cap_exceeded(product, settings):
        violations.append("shipping")

    budget = requirements.get("budget")
    budget_min = requirements.get("budget_min")
    has_budget = (
        not requirements.get("budget_unlimited")
        and (budget is not None or budget_min is not None)
    )
    if not requirements.get("budget_unlimited"):
        price = product.get("price")
        landed = product.get("landed_cost")
        price_cap = landed
        if price_cap is None:
            price_cap = (
                product.get("price_max")
                if product.get("price_is_range")
                else price
            )
        currency = normalize_currency(product.get("currency"))
        session_currency = normalize_currency(settings.get("currency"))
        budget_currency = normalize_currency(
            requirements.get("budget_currency") or session_currency
        )
        if has_budget and (
            not budget_currency
            or not currency
            or currency != budget_currency
            or (
                session_currency is not None
                and session_currency != budget_currency
            )
        ):
            violations.append("currency")
        else:
            if budget is not None and (
                price_cap is None or float(price_cap) > float(budget)
            ):
                violations.append("budget")
            min_value = landed if landed is not None else price
            if budget_min is not None and (
                min_value is None or float(min_value) < float(budget_min)
            ):
                violations.append("budget_min")
            if has_budget and product.get("listing_type") == "auction":
                violations.append("auction")
            if has_budget and product.get("cost_complete") is False:
                violations.append("cost_incomplete")

    if (
        shipping_caps_configured
        and not has_budget
        and product.get("cost_complete") is False
    ):
        violations.append("cost_incomplete")

    if include_fit and needs_fit(requirements):
        fit = _current_fit(product, requirements)
        if fit is None:
            violations.append("unjudged")
        else:
            if (
                requirements.get("category")
                and fit.get("category_ok") is not True
            ):
                violations.append("category")
            verdicts = fit.get("must_have") or {}
            for item in requirements.get("must_have") or []:
                if verdicts.get(str(item)) is not True:
                    violations.append(f"must_have:{item}")

    if require_detail and not product.get("detail_fetched"):
        violations.append("detail")

    return EligibilityResult(ok=not violations, violations=violations)


def check_product(
    product: dict[str, Any],
    requirements: dict[str, Any],
    *,
    settings: dict[str, Any],
    sites: list[str],
    require_detail: bool = False,
    include_fit: bool = True,
) -> EligibilityResult:
    return check_eligibility(
        product,
        requirements,
        settings=settings,
        sites=sites,
        require_detail=require_detail,
        include_fit=include_fit,
    )


def must_have_match_count(
    product: dict[str, Any], requirements: dict[str, Any]
) -> int:
    fit = _current_fit(product, requirements or {})
    if fit is None:
        return 0
    verdicts = fit.get("must_have") or {}
    return sum(
        1
        for item in (requirements or {}).get("must_have") or []
        if verdicts.get(str(item)) is True
    )
