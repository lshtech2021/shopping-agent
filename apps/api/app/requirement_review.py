"""LLM review of shopping hard requirements.

Regex cannot reliably distinguish an objective specification from a
subjective quality goal, so the chat model classifies each existing
must-have item. The reviewer never adds requirements; it only routes the
provided items to hard, soft, or dropped buckets.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app import llm

logger = logging.getLogger(__name__)

_SYSTEM = """You review shopping hard requirements for a buying assistant.
Return ONLY JSON:
{
  "hard_must_have": ["..."],
  "soft_must_have": ["..."],
  "dropped_must_have": ["..."],
  "clarify_question": "..." | null,
  "clarify_options": ["..."]
}

Classify every provided must-have item exactly once:
- hard: objective and verifiable from a product title, attributes, or detail
  page (for example "32G内存", "防水", "i7", "1TB SSD").
- soft: subjective quality/performance language that a listing cannot prove
  (for example "性能足够高", "好用", "性价比高").
- dropped: no restriction at all (for example "不限品牌", "不限二手",
  "无所谓").

Rules:
- Never add a new requirement; use only the items provided.
- Move a soft goal to soft_must_have; do not silently keep it as hard.
- If soft items exist, write one concise clarifying question in the user's
  language and 2-4 measurable options.
"""


class RequirementReview(BaseModel):
    hard_must_have: list[str] = Field(default_factory=list)
    soft_must_have: list[str] = Field(default_factory=list)
    dropped_must_have: list[str] = Field(default_factory=list)
    clarify_question: str | None = None
    clarify_options: list[str] = Field(default_factory=list)


def build_review_model() -> Any:
    return llm.build_chat_model()


def _content_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, list):
        content = "".join(
            str(part.get("text", "")) if isinstance(part, dict) else str(part)
            for part in content
        )
    text = str(content or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _coerce(response: Any) -> RequirementReview:
    if isinstance(response, RequirementReview):
        return response
    if isinstance(response, dict):
        return RequirementReview.model_validate(response)
    return RequirementReview.model_validate_json(_content_text(response))


async def review_requirements(
    requirements: dict[str, Any],
    *,
    model: Any = None,
) -> RequirementReview | None:
    """Classify existing must-have items with the LLM.

    Returns ``None`` when there is nothing to review or the review fails; the
    caller must then leave the requirements unchanged.
    """
    items = [
        str(item).strip()
        for item in (requirements.get("must_have") or [])
        if str(item).strip()
    ]
    if not items:
        return None

    try:
        model = model if model is not None else build_review_model()
    except Exception as exc:  # noqa: BLE001 — review is best effort
        logger.warning("requirement review unavailable: %s", exc)
        return None

    payload = {
        "must_have": items,
        "preferences": list(requirements.get("preferences") or []),
        "category": requirements.get("category"),
        "use_case": requirements.get("use_case"),
        "raw_requirement": requirements.get("raw"),
    }
    messages = [
        SystemMessage(content=_SYSTEM),
        HumanMessage(
            content=json.dumps(payload, ensure_ascii=False, default=str)
        ),
    ]

    try:
        if hasattr(model, "with_structured_output"):
            try:
                structured = model.with_structured_output(RequirementReview)
                return _coerce(await structured.ainvoke(messages))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "requirement review structured output unavailable: %s",
                    exc,
                )
        return _coerce(await model.ainvoke(messages))
    except Exception as exc:  # noqa: BLE001 — review must not kill the turn
        logger.warning("requirement review failed: %s", exc)
        return None
