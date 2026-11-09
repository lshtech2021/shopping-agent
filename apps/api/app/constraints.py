"""Deterministic product eligibility checks shared by inventory and recommendation."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_RAM_REQUIRED = (
    re.compile(
        r"(\d{1,3})\s*g(?:b)?\s*(?:内存|ram|运存)", re.I
    ),
    re.compile(
        r"(?:内存|ram|运存)\s*(?:至少|不低于|不少于|>=|≥)?\s*(\d{1,3})\s*g(?:b)?",
        re.I,
    ),
)
_RAM_LABELED = (
    re.compile(
        r"(?:内存|ram|运存|memory)\s*(?:size|容量)?\s*[:：]?\s*(\d{1,3})\s*g(?:b)?",
        re.I,
    ),
    re.compile(
        r"(\d{1,3})\s*g(?:b)?\s*(?:内存|ram|运存)", re.I
    ),
)
_STORAGE = re.compile(
    r"(?<![\d.])(\d{1,3})\s*(g|t)(?:b)?(?![\d.])", re.I
)
_COMMON_RAM = {8, 16, 32, 64}
_NEGATION_PREFIX = re.compile(
    r"(?:不|非|无|没有|不支持|not|no|non)[\s-]*$", re.I
)
_NEGATIVE_VALUE = re.compile(
    r"^(?:否|不是|不支持|不具备|不含|没有|none\b|no\b|false\b|n/?a\b|0(?:\.0+)?\b)",
    re.I,
)
_NEGATIVE_VALUES = {"否", "无", "非", "不", "no", "none", "false", "n/a", "0"}
_GLOBAL_ACCESSORY = re.compile(
    r"贴纸|贴膜|保护膜|配件|accessor(?:y|ies)|screen protector|sticker",
    re.I,
)

# Canonical category -> positive product terms and accessory/wrong-item terms.
CATEGORY_RULES: dict[str, dict[str, str]] = {
    "笔记本": {
        "positive": (
            r"笔记本|laptop|notebook|thinkpad|macbook|ideapad|"
            r"开发本|轻薄本|游戏本|办公本|超极本|二合一|小新"
        ),
        "negative": (
            r"贴纸|贴膜|保护膜|保护套|内胆包|电脑包|键盘膜|防尘塞|"
            r"散热支架|电源适配器|充电器|扩展坞|内存条|硬盘盒|数据线|配件"
        ),
    },
    "手机": {
        "positive": r"手机|phone|smartphone|iphone|android|galaxy|pixel",
        "negative": r"手机壳|贴膜|保护膜|保护套|充电器|数据线|配件",
    },
    "平板": {
        "positive": r"平板|tablet|ipad",
        "negative": r"保护套|保护膜|贴膜|键盘套|触控笔|配件",
    },
    "耳机": {
        "positive": r"耳机|headphone|earphone|earbud|airpods",
        "negative": r"保护套|耳罩|耳塞套|配件",
    },
    "显示器": {
        "positive": r"显示器|monitor",
        "negative": r"支架|贴膜|保护膜|配件",
    },
    "台式机": {
        "positive": r"台式机|desktop|主机",
        "negative": r"配件|内存条|硬盘|电源",
    },
    "相机": {
        "positive": r"相机|camera",
        "negative": r"相机包|镜头盖|贴膜|配件",
    },
    "键盘": {
        "positive": r"键盘|keyboard",
        "negative": r"键盘膜|键帽|贴纸|配件",
    },
    "鞋": {
        "positive": r"跑鞋|球鞋|运动鞋|shoes?|sneakers?",
        "negative": r"鞋带|鞋垫|鞋盒|配件",
    },
    "背包": {
        "positive": r"背包|双肩包|backpack",
        "negative": r"挂件|配件",
    },
}


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

    category = requirements.get("category")
    if category and not _matches_category(product, str(category)):
        violations.append("category")

    for item in requirements.get("must_have") or []:
        if not _matches_must_have(product, str(item)):
            violations.append(f"must_have:{item}")

    if require_detail and not product.get("detail_fetched"):
        violations.append("detail")

    return EligibilityResult(ok=not violations, violations=violations)


def must_have_match_count(
    product: dict[str, Any], requirements: dict[str, Any]
) -> int:
    return sum(
        1
        for item in (requirements or {}).get("must_have") or []
        if _matches_must_have(product, str(item))
    )


def _matches_category(product: dict[str, Any], category: str) -> bool:
    rule = CATEGORY_RULES.get(category)
    text = _product_text(product)
    if rule is None:
        normalized = _normalize(category)
        return bool(normalized) and normalized in _normalize(text)
    if _GLOBAL_ACCESSORY.search(text):
        return False
    if rule.get("negative") and re.search(rule["negative"], text, re.I):
        return False
    return bool(re.search(rule["positive"], text, re.I))


def _matches_must_have(product: dict[str, Any], item: str) -> bool:
    required_ram = _required_ram_gb(item)
    text = _product_text(product)
    if required_ram is not None:
        product_ram = _product_ram_gb(text)
        return product_ram is not None and product_ram >= required_ram

    normalized = _normalize(item)
    if not normalized:
        return False
    attribute_state = _attribute_state(product, normalized)
    if attribute_state == "negative":
        return False
    if attribute_state == "positive":
        return True
    for segment in _product_segments(product):
        if _has_positive_occurrence(segment, normalized):
            return True
    return False


def _attribute_state(
    product: dict[str, Any], normalized_term: str
) -> str | None:
    """Return positive/negative when an attribute explicitly names the term."""
    state: str | None = None
    sources = [product.get("attributes")]
    detail = product.get("detail")
    if isinstance(detail, dict):
        sources.append(detail.get("attributes"))
    for attributes in sources:
        if not isinstance(attributes, dict):
            continue
        for attr_key, attr_value in attributes.items():
            key_text = str(attr_key)
            value_text = str(attr_value)
            key_matches = normalized_term in _normalize(key_text)
            value_matches = normalized_term in _normalize(value_text)
            if not key_matches and not value_matches:
                continue
            if _is_negative_value(value_text):
                state = "negative"
                continue
            if value_matches and _has_positive_occurrence(
                value_text, normalized_term
            ):
                return "positive"
            if key_matches and state is None:
                state = "positive"
    return state


def _is_negative_value(value: str) -> bool:
    cleaned = str(value or "").strip().lower()
    if not cleaned:
        return True
    if cleaned in _NEGATIVE_VALUES:
        return True
    return bool(_NEGATIVE_VALUE.match(cleaned))


def _has_positive_occurrence(text: str, normalized_term: str) -> bool:
    lowered = str(text or "").lower()
    for match in re.finditer(re.escape(normalized_term), lowered):
        prefix = lowered[max(0, match.start() - 6) : match.start()]
        if _NEGATION_PREFIX.search(prefix):
            continue
        return True
    return False


def _product_segments(product: dict[str, Any]) -> list[str]:
    segments = [str(product.get("title") or ""), str(product.get("category") or "")]
    detail = product.get("detail")
    if isinstance(detail, dict) and detail.get("title"):
        segments.append(str(detail["title"]))
    return [segment for segment in segments if segment]


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
    parts = [str(product.get("title") or ""), str(product.get("category") or "")]

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
