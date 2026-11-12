"""LLM review of shopping requirements and preferences.

Regex cannot reliably distinguish an objective specification from a
subjective quality goal, so the chat model classifies each existing
must-have item and preference. The reviewer never invents a hard requirement;
it routes items to hard/soft/dropped buckets and asks once when a vague
quality goal is the only performance signal.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app import llm

logger = logging.getLogger(__name__)

_SYSTEM = """You review shopping requirements for a buying assistant.
Return ONLY JSON:
{
  "hard_must_have": ["..."],
  "soft_must_have": ["..."],
  "dropped_must_have": ["..."],
  "dropped_preferences": ["..."],
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

Then review preferences:
- If a preference is already covered by a hard must-have (e.g. preference
  "性能足够高" with hard must-have "CPU i7" or "32G内存"), do not ask about it
  and do not turn it into another requirement.
- If must_have is empty and a preference is a vague quality/performance goal,
  ask ONE concise question in the user's language with 2-4 measurable
  options. Do not silently invent a threshold.
- Put no-restriction phrases such as "不限品牌", "不限二手", "都可以" in
  dropped_preferences.
- Leave other preferences unchanged; do not add new requirements.

Rules:
- Never add a new hard requirement; use only the items provided.
- Move a soft must-have goal to soft_must_have; do not silently keep it hard.
- Ask at most one clarifying question for the whole review.
"""


class RequirementReview(BaseModel):
    hard_must_have: list[str] = Field(default_factory=list)
    soft_must_have: list[str] = Field(default_factory=list)
    dropped_must_have: list[str] = Field(default_factory=list)
    dropped_preferences: list[str] = Field(default_factory=list)
    clarify_question: str | None = None
    clarify_options: list[str] = Field(default_factory=list)


_NO_STRUCTURED_OUTPUT = "_sa_no_structured_output"
_DEFAULT_MODEL: tuple[Any, Any] | None = None


def build_review_model() -> Any:
    return llm.build_chat_model()


def _structured_unavailable(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "response_format" in text or "invalid_request_error" in text


def _default_review_model() -> Any:
    """Reuse one model per builder so a provider-wide structured-output
    failure is not retried on every update_requirements call."""
    global _DEFAULT_MODEL
    builder = build_review_model
    if _DEFAULT_MODEL is None or _DEFAULT_MODEL[0] is not builder:
        _DEFAULT_MODEL = (builder, builder())
    return _DEFAULT_MODEL[1]


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
    on_event: Any = None,
) -> RequirementReview | None:
    """Classify existing must-have items and preferences with the LLM.

    Returns ``None`` when there is nothing to review or the review fails; the
    caller must then leave the requirements unchanged.
    """
    items = [
        str(item).strip()
        for item in (requirements.get("must_have") or [])
        if str(item).strip()
    ]
    preferences = [
        str(item).strip()
        for item in (requirements.get("preferences") or [])
        if str(item).strip()
    ]
    if not items and not preferences:
        return None

    try:
        model = model if model is not None else _default_review_model()
    except Exception as exc:  # noqa: BLE001 — review is best effort
        logger.warning("requirement review unavailable: %s", exc)
        return None

    # Preferences are soft context. Sending them to a reviewer whose job is to
    # classify must-have items made it ask again about a preference the user
    # had already turned into a concrete must-have.
    payload = {
        "must_have": items,
        "preferences": preferences,
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
        supports_structured = hasattr(
            model, "with_structured_output"
        ) and not getattr(model, _NO_STRUCTURED_OUTPUT, False)
        if supports_structured:
            try:
                structured = model.with_structured_output(RequirementReview)
                return _coerce(await structured.ainvoke(messages))
            except Exception as exc:  # noqa: BLE001
                if _structured_unavailable(exc):
                    try:
                        setattr(model, _NO_STRUCTURED_OUTPUT, True)
                    except Exception:  # noqa: BLE001 — immutable wrappers
                        pass
                logger.warning(
                    "requirement review structured output unavailable: %s",
                    exc,
                )
                if callable(on_event):
                    on_event(
                        "provider.fallback",
                        {
                            "component": "requirement_review",
                            "error_code": "provider_structured_output_unsupported",
                            "error": str(exc)[:300],
                        },
                    )
        return _coerce(await model.ainvoke(messages))
    except Exception as exc:  # noqa: BLE001 — review must not kill the turn
        logger.warning("requirement review failed: %s", exc)
        if callable(on_event):
            on_event(
                "provider.error",
                {
                    "component": "requirement_review",
                    "error_code": "requirement_review_failed",
                    "error": str(exc)[:300],
                },
            )
        return None
