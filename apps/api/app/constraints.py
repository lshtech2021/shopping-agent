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
    require_detail: bool = False,
    include_fit: bool = True,
) -> EligibilityResult:
    requirements = requirements or {}
    violations: list[str] = []

    active_site = str(settings.get("site") or "")
    if not active_site or product.get("source") != active_site:
        violations.append("site")

    if product.get("availability") == "unavailable":
        violations.append("availability")

    condition = str(product.get("condition") or "").strip().lower()
    if condition in _PARTS_CONDITIONS:
        violations.append("parts")

    budget = requirements.get("budget")
    budget_min = requirements.get("budget_min")
    # Unlimited means "no upper cap"; it must not disable an explicit lower
    # bound or the currency needed to compare that lower bound.
    has_upper = (
        not requirements.get("budget_unlimited") and budget is not None
    )
    has_lower = budget_min is not None
    has_budget = has_upper or has_lower
    if has_budget:
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
        if (
            not session_currency
            or not currency
            or currency != session_currency
        ):
            violations.append("currency")
        else:
            if has_upper and (
                price_cap is None or float(price_cap) > float(budget)
            ):
                violations.append("budget")
            min_value = landed if landed is not None else price
            if has_lower and (
                min_value is None or float(min_value) < float(budget_min)
            ):
                violations.append("budget_min")

    if active_site:
        from app.domains.shopping.adapters import get_adapter

        try:
            adapter = get_adapter(active_site)
        except ValueError:
            adapter = None
        if adapter is not None:
            violations.extend(
                adapter.eligibility_extra(product, settings, requirements)
            )

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
    require_detail: bool = False,
    include_fit: bool = True,
) -> EligibilityResult:
    return check_eligibility(
        product,
        requirements,
        settings=settings,
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
