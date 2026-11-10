"""Agent entrypoint: one turn per user message / resume.

Policy is always the LangGraph LLM tool loop (``llm_agent``). Hard guarantees
(quotas, risk-site lockout, recommend id validation) live in ``ToolBox``.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app import llm
from app.config import settings
from app.store import Session, store
from app.tools import ToolBox
from app.trace import log_event, turn_logs

logger = logging.getLogger(__name__)


async def durable_touch(session: Session) -> None:
    """Persist a session without blocking the agent event loop."""
    await asyncio.to_thread(store.touch, session, durable=True)


async def run_agent(
    session: Session,
    content: str,
    *,
    checkpoint_resume: bool = False,
) -> dict[str, Any]:
    text = (content or "").strip()

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
            data={"timeout_s": settings.agent_turn_timeout_s},
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
            data={"policy": "llm"},
        )
        session.status = "interrupted"
        session.interrupt_reason = "agent_error"
        result = await interrupt_response_async(
            session,
            f"执行出错（{type(exc).__name__}），可点「继续」重试。",
            "agent_error",
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
        data={"question": question, "reason": reason},
    )
    session.messages.append({"role": "assistant", "content": question})
    store.touch(session, durable=True)
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
        data={"question": question, "reason": reason},
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
