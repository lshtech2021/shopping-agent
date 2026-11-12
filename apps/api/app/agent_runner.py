"""Agent entrypoint: one turn per user message / resume.

Policy is always the LangGraph LLM tool loop (``llm_agent``). Hard guarantees
(quotas, risk-site lockout, recommend id validation) live in ``ToolBox``.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app import llm, metrics
from app.config import settings
from app.store import Session, store
from app.tools import ToolBox
from app.trace import log_event, turn_logs

logger = logging.getLogger(__name__)


async def durable_touch(session: Session) -> None:
    """Persist a session without blocking the agent event loop."""
    await asyncio.to_thread(
        store.touch, session, durable=True, retry_conflict=True
    )


def _turn_summary(
    session: Session,
    result: dict[str, Any],
    *,
    started: float,
    product_count_before: int,
) -> None:
    events = turn_logs(session, limit=400)
    tool_counts: dict[str, int] = {}
    tool_status: dict[str, int] = {}
    error_codes: dict[str, int] = {}
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    excluded: dict[str, int] = {}
    extension_version = None
    model = None
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    components: dict[str, int] = {}
    internal_calls = 0
    internal_tokens = 0
    structured_fallback_calls = 0
    for event in events:
        stage = str(event.get("stage") or "")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        if data.get("extension_version"):
            extension_version = data["extension_version"]
        if stage == "llm.call":
            if data.get("structured") is False:
                structured_fallback_calls += 1
            model = data.get("model") or model
            component = str(data.get("component") or "main")
            components[component] = components.get(component, 0) + 1
            if component != "main":
                internal_calls += 1
                internal_tokens += int(data.get("total_tokens") or 0)
            prompt_tokens += int(data.get("prompt_tokens") or 0)
            completion_tokens += int(data.get("completion_tokens") or 0)
            total_tokens += int(data.get("total_tokens") or 0)
        if stage.startswith("llm.tool."):
            tool = stage.split(".", 2)[-1]
            tool_counts[tool] = tool_counts.get(tool, 0) + 1
            status = str(data.get("status") or "ok")
            key = f"{tool}:{status}"
            tool_status[key] = tool_status.get(key, 0) + 1
            code = data.get("error_code")
            if code:
                error_codes[str(code)] = error_codes.get(str(code), 0) + 1
            result_data = data.get("result")
            if (
                stage == "llm.tool.query_inventory"
                and isinstance(result_data, dict)
                and isinstance(result_data.get("excluded"), dict)
            ):
                excluded = dict(result_data["excluded"])
        level = str(event.get("level") or "")
        if level in {"warning", "error"}:
            item = {
                "stage": stage,
                "message": str(event.get("message") or "")[:200],
            }
            if data.get("error_code"):
                item["error_code"] = data["error_code"]
            (errors if level == "error" else warnings).append(item)

    status = str(result.get("status") or session.status)
    summary = {
        "status": status,
        "interrupted": bool(result.get("interrupted")),
        "interrupt_reason": result.get("interrupt_reason"),
        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        "llm": {
            "model": model,
            "calls": sum(1 for e in events if e.get("stage") == "llm.call"),
            "steps": sum(1 for e in events if e.get("stage") == "llm.step"),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "components": components,
            "internal_calls": internal_calls,
            "internal_tokens": internal_tokens,
            # Count actual fallback calls as well as provider warnings. Some
            # providers/fit-judge calls use cached text fallbacks without
            # emitting a new warning on every call.
            "structured_fallbacks": max(
                sum(
                    1
                    for item in warnings
                    if item.get("error_code")
                    == "provider_structured_output_unsupported"
                    or "structured output" in item["message"].lower()
                ),
                structured_fallback_calls,
            ),
            "structured_fallback_calls": structured_fallback_calls,
        },
        "tools": tool_counts,
        "tool_status": tool_status,
        "error_codes": error_codes,
        "inventory": {
            "product_count_before": product_count_before,
            "product_count": len(session.products),
            "added": len(session.products) - product_count_before,
            "excluded": excluded,
        },
        "recommendations": len(
            result.get("recommendations") or session.recommendations or []
        ),
        "warnings": warnings[-10:],
        "errors": errors[-10:],
        "settings": {
            "site": session.settings.get("site"),
            "currency": session.settings.get("currency"),
            "max_shipping_ratio": session.settings.get("max_shipping_ratio"),
            "max_shipping_cost": session.settings.get("max_shipping_cost"),
        },
        "versions": {
            "llm_model": model or settings.llm_model,
            "extension": extension_version,
        },
    }
    log_event(
        session,
        "Agent 回合摘要",
        stage="turn.summary",
        source="agent",
        level="warning" if errors or summary["interrupted"] else "info",
        data=summary,
    )
    metrics.record_turn(summary)


async def run_agent(
    session: Session,
    content: str,
    *,
    checkpoint_resume: bool = False,
) -> dict[str, Any]:
    text = (content or "").strip()
    started = time.perf_counter()
    product_count_before = len(session.products)

    session.turn += 1
    session.status = "running"
    session.interrupt_reason = None
    session.quotas = {}  # design §6: quotas are per turn, not per session
    tools = ToolBox(session)
    log_event(
        session,
        "Agent 回合开始",
        stage="agent.run",
        source="agent",
        data={
            "policy": "llm",
            "content": text[:200],
            "sites": session.sites,
            "searched_sites": session.searched_sites,
            "product_count": len(session.products),
            "mock_browser": tools.mock_mode,
            "llm_enabled": llm.llm_enabled(),
            "llm_model": settings.llm_model,
            "settings": dict(session.settings),
        },
        persist=True,
    )

    if not llm.llm_enabled():
        if text and session.pending_questions:
            session.pending_questions = []
        session.status = "interrupted"
        session.interrupt_reason = "llm_unavailable"
        result = await interrupt_response_async(
            session,
            "未配置 SA_LLM_API_KEY，无法运行选品助手。请在 apps/api/.env 中设置后重试。",
            "llm_unavailable",
        )
        _turn_summary(
            session,
            result,
            started=started,
            product_count_before=product_count_before,
        )
        await durable_touch(session)
        result["execution_logs"] = turn_logs(session)
        return result

    try:
        from app.llm_agent import run_llm_policy

        result = await asyncio.wait_for(
            run_llm_policy(
                session,
                text,
                tools,
                checkpoint_resume=checkpoint_resume,
            ),
            timeout=settings.agent_turn_timeout_s,
        )
    except asyncio.TimeoutError:
        log_event(
            session,
            "Agent 回合超时",
            stage="agent.timeout",
            source="agent",
            level="error",
            data={
                "timeout_s": settings.agent_turn_timeout_s,
                "error_code": "turn_timeout",
            },
        )
        session.status = "interrupted"
        session.interrupt_reason = "turn_timeout"
        result = await interrupt_response_async(
            session,
            "这一轮执行时间过长，已中止。请缩小需求或点「继续」重试。",
            "turn_timeout",
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("agent turn failed sid=%s", session.id[:8])
        log_event(
            session,
            f"回合异常：{type(exc).__name__}: {exc}",
            stage="agent.error",
            source="agent",
            level="error",
            data={"policy": "llm", "error_code": "agent_error"},
        )
        session.status = "interrupted"
        session.interrupt_reason = "agent_error"
        result = await interrupt_response_async(
            session,
            f"执行出错（{type(exc).__name__}），可点「继续」重试。",
            "agent_error",
        )

    _turn_summary(
        session,
        result,
        started=started,
        product_count_before=product_count_before,
    )
    await durable_touch(session)
    result["execution_logs"] = turn_logs(session)
    return result


async def resume_agent(session: Session, note: str) -> dict[str, Any]:
    cleared = dict(session.blocked_sites)
    session.blocked_sites.clear()
    log_event(
        session,
        "恢复中断回合（用户已处理风控）" if cleared else "恢复中断回合",
        stage="agent.resume",
        source="agent",
        data={"note": note, "cleared_sites": cleared},
    )
    content = (
        note.strip()
        if note and note.strip() not in {"continue", "user continued"}
        else ""
    )
    return await run_agent(session, content, checkpoint_resume=True)


def interrupt_response(session: Session, question: str, reason: str) -> dict[str, Any]:
    log_event(
        session,
        f"中断：{reason}",
        stage="agent.interrupt",
        source="agent",
        level="warning",
        data={
            "question": question,
            "reason": reason,
            "error_code": reason,
        },
    )
    session.messages.append({"role": "assistant", "content": question})
    store.touch(session, durable=True, retry_conflict=True)
    return {
        "session_id": session.id,
        "status": session.status,
        "reply": question,
        "recommendations": session.recommendations,
        "interrupted": True,
        "interrupt_reason": reason,
    }


async def interrupt_response_async(
    session: Session, question: str, reason: str
) -> dict[str, Any]:
    log_event(
        session,
        f"中断：{reason}",
        stage="agent.interrupt",
        source="agent",
        level="warning",
        data={
            "question": question,
            "reason": reason,
            "error_code": reason,
        },
    )
    session.messages.append({"role": "assistant", "content": question})
    await durable_touch(session)
    return {
        "session_id": session.id,
        "status": session.status,
        "reply": question,
        "recommendations": session.recommendations,
        "interrupted": True,
        "interrupt_reason": reason,
    }
