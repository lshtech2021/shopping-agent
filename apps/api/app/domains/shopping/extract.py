"""Extract shopping requirement patches from user text (LLM or temporary regex fallback)."""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError

from app.domains.shopping.requirements import (
    _extract_category,
    _extract_min_ram_gb,
    parse_budget,
)
from app.domains.shopping.requirements_patch import RequirementPatch

LlmFn = Callable[[str], Awaitable[Any]]

_PATCH_SCHEMA_HINT = json.dumps(
    {
        "budget_cny": None,
        "budget_min_cny": None,
        "budget_unlimited": None,
        "category": None,
        "use_case": None,
        "must_have": [],
        "preferences": [],
        "confidence": {"budget_cny": "high"},
        "ambiguous_fields": [],
        "source_spans": {},
    },
    ensure_ascii=False,
)


def _build_prompt(text: str, current: dict) -> str:
    current_json = json.dumps(current or {}, ensure_ascii=False)
    return (
        "Extract only shopping requirements explicitly stated in the user message. "
        "Do not invent constraints. Return JSON matching this shape:\n"
        f"{_PATCH_SCHEMA_HINT}\n\n"
        f"Current requirements: {current_json}\n"
        f"User message: {text.strip()}"
    )


def _fallback_extract(text: str) -> RequirementPatch:
    """Temporary offline extractor; remove in Task 6 when agent-driven patches land."""
    budget = parse_budget(text)
    confidence: dict[str, str] = {}
    patch_kw: dict[str, Any] = {
        "ambiguous_fields": [],
        "must_have": [],
        "preferences": [],
    }
    if budget.max_cny is not None:
        patch_kw["budget_cny"] = budget.max_cny
        confidence["budget_cny"] = budget.confidence
    if budget.min_cny is not None:
        patch_kw["budget_min_cny"] = budget.min_cny
        confidence["budget_min_cny"] = budget.confidence
    category = _extract_category(text)
    if category:
        patch_kw["category"] = category
        confidence["category"] = "high"
    ram_gb = _extract_min_ram_gb(text)
    if ram_gb is not None:
        patch_kw["must_have"] = [f"{ram_gb}G内存"]
        confidence["must_have"] = "high"
    patch_kw["confidence"] = confidence
    return RequirementPatch.model_validate(patch_kw)


async def extract_requirement_patch(
    text: str,
    *,
    current: dict,
    llm: LlmFn | None = None,
) -> RequirementPatch:
    if llm is None:
        return _fallback_extract(text)

    try:
        raw = await llm(_build_prompt(text, current))
        if not isinstance(raw, dict):
            return RequirementPatch(ambiguous_fields=["_extract_failed"])
        return RequirementPatch.model_validate(raw)
    except (ValidationError, TypeError, ValueError):
        return RequirementPatch(ambiguous_fields=["_extract_failed"])
