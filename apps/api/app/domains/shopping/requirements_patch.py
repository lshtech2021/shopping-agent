# requirements_patch.py
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Confidence = Literal["high", "low"]


class RequirementPatch(BaseModel):
    budget: float | None = None
    budget_min: float | None = None
    budget_unlimited: bool | None = None
    category: str | None = None
    use_case: str | None = None
    must_have: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    confidence: dict[str, Confidence] = Field(default_factory=dict)
    ambiguous_fields: list[str] = Field(default_factory=list)
    source_spans: dict[str, str] = Field(default_factory=dict)


def apply_requirement_patch(
    requirements: dict[str, Any],
    patch: RequirementPatch,
    *,
    replace_must_have: bool | None = None,
) -> dict[str, Any]:
    req = dict(requirements or {})
    conf = patch.confidence or {}
    if replace_must_have is None:
        replace_must_have = "must_have" in patch.model_fields_set

    def high(field: str) -> bool:
        return conf.get(field, "high") == "high" and field not in patch.ambiguous_fields

    if patch.budget_unlimited is True and high("budget_unlimited"):
        # Unlimited is an upper-bound stance only. A lower bound is a
        # separate constraint and must survive when the user says "no upper
        # limit, but keep the minimum".
        req["budget"] = None
        req["budget_unlimited"] = True
        if patch.budget_min is not None and high("budget_min"):
            req["budget_min"] = float(patch.budget_min)
    else:
        if patch.budget is not None and high("budget"):
            req["budget"] = float(patch.budget)
            req["budget_unlimited"] = False
        if patch.budget_min is not None and high("budget_min"):
            req["budget_min"] = float(patch.budget_min)
    req.pop("budget_currency", None)
    if patch.category and high("category"):
        req["category"] = patch.category
    if patch.use_case and high("use_case"):
        req["use_case"] = patch.use_case
    if replace_must_have:
        # The caller (the LLM) supplies the complete desired list. Replacing
        # rather than merging keeps semantic conflicts out of the backend;
        # deciding whether an item supersedes another is an LLM judgment.
        req["must_have"] = list(
            dict.fromkeys(
                str(item) for item in (patch.must_have or []) if item
            )
        )
    if patch.preferences:
        prefs = list(req.get("preferences") or [])
        for item in patch.preferences:
            if item and item not in prefs:
                prefs.append(item)
        req["preferences"] = prefs
    return req
