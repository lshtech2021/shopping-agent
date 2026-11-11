# requirements_patch.py
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.currency import normalize_currency

Confidence = Literal["high", "low"]
_RAM_REQUIREMENT = re.compile(r"\d{1,3}\s*G内存", re.I)


class RequirementPatch(BaseModel):
    budget: float | None = None
    budget_min: float | None = None
    budget_unlimited: bool | None = None
    budget_currency: str | None = None
    category: str | None = None
    use_case: str | None = None
    must_have: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    confidence: dict[str, Confidence] = Field(default_factory=dict)
    ambiguous_fields: list[str] = Field(default_factory=list)
    source_spans: dict[str, str] = Field(default_factory=dict)


def apply_requirement_patch(
    requirements: dict[str, Any], patch: RequirementPatch
) -> dict[str, Any]:
    req = dict(requirements or {})
    conf = patch.confidence or {}

    def high(field: str) -> bool:
        return conf.get(field, "high") == "high" and field not in patch.ambiguous_fields

    if patch.budget_unlimited is True and high("budget_unlimited"):
        req["budget"] = None
        req["budget_min"] = None
        req["budget_currency"] = None
        req["budget_unlimited"] = True
    else:
        if patch.budget is not None and high("budget"):
            req["budget"] = float(patch.budget)
            req["budget_unlimited"] = False
        if patch.budget_min is not None and high("budget_min"):
            req["budget_min"] = float(patch.budget_min)
            req["budget_unlimited"] = False
        if patch.budget_currency and high("budget_currency"):
            code = normalize_currency(patch.budget_currency)
            if code:
                req["budget_currency"] = code
    if patch.category and high("category"):
        req["category"] = patch.category
    if patch.use_case and high("use_case"):
        req["use_case"] = patch.use_case
    if patch.must_have:
        must = list(req.get("must_have") or [])
        for item in patch.must_have:
            if item and _RAM_REQUIREMENT.search(item):
                must = [
                    existing
                    for existing in must
                    if not _RAM_REQUIREMENT.search(str(existing))
                ]
            if item and item not in must:
                must.append(item)
        req["must_have"] = must
    if patch.preferences:
        prefs = list(req.get("preferences") or [])
        for item in patch.preferences:
            if item and item not in prefs:
                prefs.append(item)
        req["preferences"] = prefs
    return req
