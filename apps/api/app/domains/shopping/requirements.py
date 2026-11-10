"""Shopping requirement parsing: budget, category, RAM, preference cues."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

_BUDGET_UNLIMITED = re.compile(
    r"(?:预算|价格|价位)[^。；;]{0,8}(?:不限|取消|不用管|无所谓|随意|越贵越好)"
    r"|(?:不限|取消|不用管|越贵越好)[^。；;]{0,6}(?:预算|价格|价位)"
    r"|^越贵越好$"
)
_QUESTION_MENTION = re.compile(r"怎么样|如何|吗|哪款|哪个|这个|这款|够用|值不值")
_SPEC_NUMBER = re.compile(
    r"(?:rtx|gtx|i[3579][- ]?|锐龙|ryzen|骁龙|snapdragon)\s*\d"
    r"|\d[\d.,]*\s*(?:g|gb|t|tb|mah|像素|万像素|个|件|台|年|款|代|核|hz|k|寸|英寸|mm|cm|w|v)\b",
    re.I,
)

_RAM_MUST = re.compile(r"(\d{1,3})\s*G内存", re.I)
_CN_DIGITS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_UNIT = {"万": 10000.0, "千": 1000.0, "k": 1000.0, "K": 1000.0, "": 1.0}


@dataclass(frozen=True)
class BudgetParse:
    max_cny: float | None
    min_cny: float | None
    confidence: Literal["high", "low"]


_NON_BUDGET_TAIL = re.compile(
    r"^\s*(?:万)?(?:像素|mAh|mah|个|件|台|年|款|代|g(?:b)?(?![\d.]))",
    re.I,
)
_NUM = r"(\d+(?:\.\d+)?)"


def _has_non_budget_tail(value: str, end: int) -> bool:
    return bool(_NON_BUDGET_TAIL.match(value[end:]))


def parse_budget(text: str) -> BudgetParse:
    """Parse a CNY budget without treating specs, counters, or model numbers as money."""
    value = str(text or "").strip()
    if not value:
        return BudgetParse(None, None, "low")
    if _BUDGET_UNLIMITED.search(value):
        return BudgetParse(None, None, "high")

    def amount(number: str, unit: str | None) -> float:
        return float(number) * _UNIT.get((unit or "").lower(), 1.0)

    cue = re.search(r"预算|价格|价位|不超过|最多|控制在", value)

    # Open-ended minimum first: a currency word may sit before the marker.
    minimum = re.search(
        rf"(?:至少|不低于)\s*(?:预算)?\s*[:：]?\s*{_NUM}\s*(万|千|k)?\s*(?:元|块)?",
        value,
        re.I,
    )
    if minimum and not _has_non_budget_tail(value, minimum.end()):
        return BudgetParse(
            None,
            amount(minimum.group(1), minimum.group(2)),
            "high",
        )
    minimum_suffix = re.search(
        rf"{_NUM}\s*(万|千|k)?\s*(?:元|块)?\s*(?:以上|起|≥)",
        value,
        re.I,
    )
    if minimum_suffix and not _has_non_budget_tail(
        value, minimum_suffix.end()
    ):
        return BudgetParse(
            None,
            amount(minimum_suffix.group(1), minimum_suffix.group(2)),
            "high",
        )

    # Range: "预算3000-5000" and "预算五千到八千" use the upper bound.
    range_match = re.search(
        rf"{_NUM}\s*(万|千|k)?\s*[-~到至]\s*{_NUM}\s*(万|千|k)?",
        value,
        re.I,
    )
    if range_match:
        lower = amount(range_match.group(1), range_match.group(2))
        upper = amount(range_match.group(3), range_match.group(4))
        year_like = 1900 <= lower <= 2100 and 1900 <= upper <= 2100
        if (
            (cue or re.search(r"元|块|¥|￥|万|千|k", value, re.I))
            or (lower >= 500 and upper >= lower and not year_like)
        ) and not _has_non_budget_tail(value, range_match.end()):
            return BudgetParse(upper, None, "high")
    cn_range = re.search(
        r"([一二两三四五六七八九])\s*(千|万)\s*[-~到至]\s*"
        r"([一二两三四五六七八九])\s*(千|万)",
        value,
    )
    if cn_range and not _has_non_budget_tail(value, cn_range.end()):
        lower = _CN_DIGITS[cn_range.group(1)] * _UNIT[cn_range.group(2)]
        upper = _CN_DIGITS[cn_range.group(3)] * _UNIT[cn_range.group(4)]
        return BudgetParse(upper, None, "high")

    # "1万5" means 15000, not 10000.
    compact_wan = re.search(rf"{_NUM}\s*万\s*(\d)(?!\d)", value)
    if compact_wan and not _has_non_budget_tail(value, compact_wan.end()):
        return BudgetParse(
            float(compact_wan.group(1)) * 10000
            + int(compact_wan.group(2)) * 1000,
            None,
            "high",
        )

    # Budget cue before the number, including "价格不要超过8000".
    cue_match = re.search(
        rf"(?:预算|价格|价位)[^。；;]{{0,8}}?"
        rf"(?:不要超过|不超过|最多|控制在|改成|改为|是|为)?"
        rf"\s*[:：]?\s*{_NUM}\s*(万|千|k)?",
        value,
        re.I,
    )
    if cue_match and not _has_non_budget_tail(value, cue_match.end()):
        return BudgetParse(
            amount(cue_match.group(1), cue_match.group(2)),
            None,
            "high",
        )

    cap_before = re.search(
        rf"(?:不要超过|不超过|最多|控制在|封顶)\s*[:：]?\s*"
        rf"{_NUM}\s*(万|千|k)?",
        value,
        re.I,
    )
    if cap_before and not _has_non_budget_tail(value, cap_before.end()):
        return BudgetParse(
            amount(cap_before.group(1), cap_before.group(2)),
            None,
            "high",
        )

    cap_suffix = re.search(
        rf"{_NUM}\s*(万|千|k)?\s*(?:元|块)?\s*(?:以内|以下|封顶|内(?!存))",
        value,
        re.I,
    )
    if cap_suffix and not _has_non_budget_tail(value, cap_suffix.end()):
        return BudgetParse(
            amount(cap_suffix.group(1), cap_suffix.group(2)),
            None,
            "high",
        )

    cn = re.search(r"([一二两三四五六七八九])\s*(千|万)", value)
    if cn and not _has_non_budget_tail(value, cn.end()):
        return BudgetParse(
            _CN_DIGITS[cn.group(1)] * _UNIT[cn.group(2)],
            None,
            "high",
        )

    symbol = re.search(rf"(?:¥|￥)\s*{_NUM}", value)
    if symbol and not _has_non_budget_tail(value, symbol.end()):
        return BudgetParse(float(symbol.group(1)), None, "high")

    unit = re.search(rf"{_NUM}\s*(元|块|万|千)", value)
    if unit and not _has_non_budget_tail(value, unit.end()):
        return BudgetParse(amount(unit.group(1), unit.group(2)), None, "high")

    if cue:
        unit_k = re.search(rf"{_NUM}\s*k", value, re.I)
        if unit_k and not _has_non_budget_tail(value, unit_k.end()):
            return BudgetParse(float(unit_k.group(1)) * 1000, None, "high")

    return BudgetParse(None, None, "low")


def _extract_budget(text: str) -> float | None:
    return parse_budget(text).max_cny


# canonical category -> (regex over user text, English search term for global sites)
CATEGORIES: dict[str, tuple[str, str]] = {
    "笔记本": (r"笔记本|laptop|notebook", "laptop"),
    "手机": (r"手机|phone|smartphone", "smartphone"),
    "平板": (r"平板|tablet|ipad", "tablet"),
    "耳机": (r"耳机|headphone|earphone|earbud", "headphones"),
    "显示器": (r"显示器|monitor", "monitor"),
    "台式机": (r"台式机|desktop", "desktop computer"),
    "相机": (r"相机|camera", "camera"),
    "键盘": (r"键盘|keyboard", "keyboard"),
    "鞋": (r"跑鞋|球鞋|运动鞋|shoes?|sneakers?", "shoes"),
    "背包": (r"背包|双肩包|backpack", "backpack"),
}
def _extract_category(text: str) -> str | None:
    for name, (pattern, _en) in CATEGORIES.items():
        if re.search(pattern, text or "", re.I):
            return name
    return None


def _extract_min_ram_gb(text: str) -> int | None:
    """Only accept RAM written next to a RAM word (so '512G SSD' is never RAM)."""
    text = text or ""
    for pattern in (
        r"(?:内存|ram|运存)\s*(?:改成|改为|调整到?|提到|提高到|升到|降到|至少|不低于|不少于|>=|≥)?\s*(\d{1,3})\s*g",
        r"(?<![\d.])(\d{1,3})\s*g(?:b)?\s*(?:及以上|以上)?\s*(?:内存|ram|运存)",
    ):
        match = re.search(pattern, text, re.I)
        if match and 2 <= int(match.group(1)) <= 256:
            return int(match.group(1))
    return None
