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

FIT_KEY = "fit"


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


def check_product(
    product: dict[str, Any],
    requirements: dict[str, Any],
    *,
    sites: list[str],
    require_detail: bool = False,
) -> EligibilityResult:
    requirements = requirements or {}
    violations: list[str] = []

    if product.get("source") not in sites:
        violations.append("site")

    if not requirements.get("budget_unlimited"):
        price = product.get("price_cny")
        price_cap = (
            product.get("price_max_cny")
            if product.get("price_is_range")
            else price
        )
        budget = requirements.get("budget_cny")
        if budget is not None and (
            price_cap is None or float(price_cap) > float(budget)
        ):
            violations.append("budget")
        budget_min = requirements.get("budget_min_cny")
        if budget_min is not None and (
            price is None or float(price) < float(budget_min)
        ):
            violations.append("budget_min")

    if needs_fit(requirements):
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
