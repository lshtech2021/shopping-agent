"""Deterministic product eligibility checks shared by inventory and recommendation."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_RAM_REQUIRED = (
    re.compile(r"(\d{1,3})\s*g(?:b)?\s*(?:内存|ram|运存)", re.I),
    re.compile(r"(?:内存|ram|运存)\s*(\d{1,3})\s*g(?:b)?", re.I),
)
_RAM_LABELED = (
    re.compile(r"(?:内存|ram|运存)\s*(\d{1,3})\s*g(?:b)?", re.I),
    re.compile(r"(\d{1,3})\s*g(?:b)?\s*(?:内存|ram|运存)", re.I),
)
_STORAGE = re.compile(
    r"(?<![\d.])(\d{1,3})\s*(g|t)(?:b)?(?![\d.])", re.I
)
_COMMON_RAM = {8, 16, 32, 64}


@dataclass(frozen=True)
class EligibilityResult:
    ok: bool
    violations: list[str]


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
        budget = requirements.get("budget_cny")
        if budget is not None and (
            price is None or float(price) > float(budget)
        ):
            violations.append("budget")
        budget_min = requirements.get("budget_min_cny")
        if budget_min is not None and (
            price is None or float(price) < float(budget_min)
        ):
            violations.append("budget_min")

    text = _product_text(product)
    for item in requirements.get("must_have") or []:
        if not _matches_must_have(text, str(item)):
            violations.append(f"must_have:{item}")

    if require_detail and not product.get("detail_fetched"):
        violations.append("detail")

    return EligibilityResult(ok=not violations, violations=violations)


def must_have_match_count(
    product: dict[str, Any], requirements: dict[str, Any]
) -> int:
    text = _product_text(product)
    return sum(
        1
        for item in (requirements or {}).get("must_have") or []
        if _matches_must_have(text, str(item))
    )


def _matches_must_have(text: str, item: str) -> bool:
    required_ram = _required_ram_gb(item)
    if required_ram is not None:
        product_ram = _product_ram_gb(text)
        return product_ram is not None and product_ram >= required_ram

    normalized = _normalize(item)
    return bool(normalized) and normalized in _normalize(text)


def _required_ram_gb(item: str) -> int | None:
    for pattern in _RAM_REQUIRED:
        match = pattern.search(item)
        if match:
            value = int(match.group(1))
            if 2 <= value <= 256:
                return value
    return None


def _product_ram_gb(text: str) -> int | None:
    for pattern in _RAM_LABELED:
        match = pattern.search(text)
        if match:
            value = int(match.group(1))
            if 2 <= value <= 256:
                return value

    # Unlabeled sizes are ambiguous (storage vs RAM). Only accept a common RAM
    # capacity when another, larger storage token is present (e.g. "32G 1T").
    sizes: list[tuple[int, int]] = []
    for match in _STORAGE.finditer(text):
        value = int(match.group(1))
        unit = match.group(2).lower()
        sizes.append((value, value * 1024 if unit == "t" else value))
    for value, normalized in sizes:
        if value in _COMMON_RAM and any(
            other > normalized for _, other in sizes
        ):
            return value
    return None


def _product_text(product: dict[str, Any]) -> str:
    parts = [str(product.get("title") or "")]

    attributes = product.get("attributes")
    if isinstance(attributes, dict):
        for attr_key, attr_value in attributes.items():
            parts.append(str(attr_key))
            parts.append(str(attr_value))

    detail = product.get("detail")
    if isinstance(detail, dict):
        if detail.get("title"):
            parts.append(str(detail["title"]))
        detail_attributes = detail.get("attributes")
        if isinstance(detail_attributes, dict):
            for attr_key, attr_value in detail_attributes.items():
                parts.append(str(attr_key))
                parts.append(str(attr_value))

    return " ".join(parts).lower()


def _normalize(value: str) -> str:
    return "".join(str(value or "").lower().split())
