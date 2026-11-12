"""LLM judge: does a listing fit the requested category and must-have items?

Verdicts are written to ``product["fit"]`` (see `app.constraints`). One batched
call covers many products; verdicts are cached until the requirements or the
product's detail state change.

Providers that reject structured ``response_format`` fall back to plain-text
JSON (same pattern as the shopping planner).
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, ValidationError

from app import llm
from app.constraints import fit_signature, needs_judging

logger = logging.getLogger(__name__)

BATCH_SIZE = 15
_MAX_ATTRS = 40

_SYSTEM = """You judge whether shopping listings fit a buyer's requirements.
Return ONLY JSON matching:
{"verdicts":[{"product_id":"...","category_ok":true|false|null,"must_have":[{"item":"...","ok":true|false|null}],"reason":"..."}]}
Rules:
- category_ok: true only if the product itself is the requested category. Accessories, parts, cases, films, stickers, books or unrelated items are false. Use null when the category is not requested.
- must_have: one verdict per requested item, using the item text exactly as given. true only if the listing evidence (title, attributes, detail) supports it; false if the evidence contradicts it or says it is absent/unsupported (e.g. "不防水", attribute 否/No); null if the listing does not say. A memory item like "32G内存" means at least that much RAM; do not mistake storage (SSD/HDD) for RAM.
Use only the given listing data; never assume specs that are not stated."""


class _MustHaveVerdict(BaseModel):
    item: str
    ok: Optional[bool] = None


class _ProductFit(BaseModel):
    product_id: str
    category_ok: Optional[bool] = None
    must_have: list[_MustHaveVerdict] = Field(default_factory=list)
    reason: str = ""


class FitBatch(BaseModel):
    verdicts: list[_ProductFit] = Field(default_factory=list)


_DEFAULT_MODEL: tuple[Any, Any] | None = None


def build_judge_model() -> Any:
    return llm.build_chat_model()


def _default_judge_model() -> Any:
    """Reuse one model per builder so the structured-output failure flag
    survives across query_inventory calls."""
    global _DEFAULT_MODEL
    builder = build_judge_model
    if _DEFAULT_MODEL is None or _DEFAULT_MODEL[0] is not builder:
        _DEFAULT_MODEL = (builder, builder())
    return _DEFAULT_MODEL[1]


def _listing(product: dict[str, Any]) -> dict[str, Any]:
    attributes = dict(list((product.get("attributes") or {}).items())[:_MAX_ATTRS])
    record: dict[str, Any] = {
        "product_id": product.get("product_id"),
        "title": product.get("title"),
        "category": product.get("category"),
        "attributes": attributes,
    }
    detail = product.get("detail")
    if isinstance(detail, dict):
        record["detail_title"] = detail.get("title")
        record["detail_attributes"] = dict(
            list((detail.get("attributes") or {}).items())[:_MAX_ATTRS]
        )
        if detail.get("condition"):
            record["detail_condition"] = detail.get("condition")
        highlights = detail.get("highlights") or []
        if isinstance(highlights, list) and highlights:
            record["detail_highlights"] = [
                str(item)[:200] for item in highlights[:10]
            ]
    return record


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


def _usage_fields(response: Any) -> dict[str, Any]:
    usage = getattr(response, "usage_metadata", None)
    if not isinstance(usage, dict):
        return {}
    return {
        "prompt_tokens": usage.get("input_tokens"),
        "completion_tokens": usage.get("output_tokens"),
        "total_tokens": usage.get("total_tokens"),
    }


def _coerce(response: Any) -> FitBatch:
    if isinstance(response, FitBatch):
        return response
    if isinstance(response, dict):
        return FitBatch.model_validate(response)
    text = _content_text(response)
    try:
        return FitBatch.model_validate_json(text)
    except (ValidationError, ValueError):
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise
        return FitBatch.model_validate_json(match.group(0))


def _structured_unavailable(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "response_format" in text or "invalid_request_error" in text


_NO_STRUCTURED_OUTPUT = "_sa_no_structured_output"


async def _invoke_batch(
    model: Any,
    messages: list[Any],
    on_event: Any = None,
) -> FitBatch:
    structured_exc: Exception | None = None
    model_name = getattr(model, "model_name", None) or getattr(
        model, "model", None
    )
    supports_structured = hasattr(model, "with_structured_output") and not getattr(
        model, _NO_STRUCTURED_OUTPUT, False
    )
    if supports_structured:
        started = time.perf_counter()
        try:
            structured = model.with_structured_output(FitBatch)
            response = await structured.ainvoke(messages)
            if callable(on_event):
                on_event(
                    "llm.call",
                    {
                        "component": "fit_judge",
                        "model": model_name,
                        "duration_ms": round(
                            (time.perf_counter() - started) * 1000, 1
                        ),
                        "structured": True,
                        **_usage_fields(response),
                    },
                )
            return _coerce(response)
        except Exception as exc:  # noqa: BLE001
            structured_exc = exc
            if not _structured_unavailable(exc):
                raise
            try:
                setattr(model, _NO_STRUCTURED_OUTPUT, True)
            except Exception:  # noqa: BLE001 — some wrappers are immutable
                pass
            logger.warning(
                "fit judge structured output unavailable; falling back to JSON text: %s",
                exc,
            )
            if callable(on_event):
                on_event(
                    "provider.fallback",
                    {
                        "component": "fit_judge",
                        "error_code": "provider_structured_output_unsupported",
                        "error": str(exc)[:300],
                    },
                )
    started = time.perf_counter()
    try:
        response = await model.ainvoke(messages)
        if callable(on_event):
            on_event(
                "llm.call",
                {
                    "component": "fit_judge",
                    "model": model_name,
                    "duration_ms": round(
                        (time.perf_counter() - started) * 1000, 1
                    ),
                    "structured": False,
                    **_usage_fields(response),
                },
            )
        return _coerce(response)
    except Exception as exc:
        if callable(on_event):
            on_event(
                "provider.error",
                {
                    "component": "fit_judge",
                    "error_code": "fit_judge_failed",
                    "error": str(exc)[:300],
                },
            )
        if structured_exc is not None:
            raise structured_exc
        raise


async def judge_products(
    products: list[dict[str, Any]],
    requirements: dict[str, Any],
    *,
    model: Any = None,
    on_event: Any = None,
) -> dict[str, Any]:
    """Judge every product lacking a current verdict. Never raises.

    Returns ``{"judged": int, "error": str | None}``; products the judge could
    not assess simply stay unjudged (and therefore ineligible).
    """
    targets = [p for p in products if needs_judging(p, requirements)]
    if not targets:
        return {"judged": 0, "error": None}

    judged = 0
    error: str | None = None
    try:
        model = model if model is not None else _default_judge_model()
    except Exception as exc:  # noqa: BLE001
        logger.warning("fit judge unavailable: %s", exc)
        return {"judged": 0, "error": f"{type(exc).__name__}: {exc}"}

    wanted = {
        "category": requirements.get("category"),
        "must_have": [str(item) for item in requirements.get("must_have") or []],
    }
    for start in range(0, len(targets), BATCH_SIZE):
        batch = targets[start : start + BATCH_SIZE]
        by_id = {str(p.get("product_id")): p for p in batch}
        payload = {"requirements": wanted, "products": [_listing(p) for p in batch]}
        messages = [
            SystemMessage(content=_SYSTEM),
            HumanMessage(
                content=json.dumps(payload, ensure_ascii=False, default=str)
            ),
        ]
        try:
            result = await _invoke_batch(model, messages, on_event)
        except Exception as exc:  # noqa: BLE001 — judging must not kill the turn
            logger.warning("fit judge failed: %s", exc)
            error = f"{type(exc).__name__}: {exc}"
            continue
        for verdict in result.verdicts:
            product = by_id.get(str(verdict.product_id))
            if product is None:
                continue
            product["fit"] = {
                "sig": fit_signature(product, requirements),
                "category_ok": verdict.category_ok,
                "must_have": {v.item: v.ok for v in verdict.must_have},
                "reason": verdict.reason[:200],
            }
            judged += 1
    return {"judged": judged, "error": error}
