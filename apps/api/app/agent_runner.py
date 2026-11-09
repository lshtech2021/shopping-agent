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


async def run_agent(
    session: Session,
    content: str,
    answers: dict[str, str] | None = None,
    *,
    skip_parse: bool = False,
    checkpoint_resume: bool = False,
) -> dict[str, Any]:
    session.turn += 1
    session.status = "running"
    session.interrupt_reason = None
    session.quotas = {}  # design §6: quotas are per turn, not per session
    tools = ToolBox(session)
    text = (content or session.requirement or "").strip()
    clarified = answers is not None
    clarify_result: dict[str, Any] | None = None
    if answers is not None:
        if session.pending_questions:
            clarify_result = tools.apply_clarify(answers)
        else:
            log_event(
                session,
                "收到澄清回答但没有待答问题，已忽略",
                stage="agent.clarify",
                source="agent",
                level="warning",
                data={"answers": answers},
            )
    clarify_failed = bool(
        clarify_result and clarify_result.get("needs_clarify")
    )
    if clarify_failed:
        # Host applied what it could; re-asking is a model tool decision.
        text = (
            f"{session.requirement or text}\n"
            "（解析提示：上一轮澄清回答无法解析为有效条件，"
            "请再次调用 clarify，不要自行编造预算或其他硬约束。）"
        ).strip()
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
            "clarify_failed": clarify_failed,
        },
        persist=True,
    )

    if not llm.llm_enabled():
        ask = tools.ask_user(
            "未配置 SA_LLM_API_KEY，无法运行选品助手。请在 apps/api/.env 中设置后重试。",
            "llm_unavailable",
        )
        result = interrupt_response(session, ask["question"], ask["reason"])
        store.touch(session)
        result["execution_logs"] = turn_logs(session)
        return result

    try:
        from app.llm_agent import run_llm_policy

        # Successful clarify answers skip re-parse; failed ones must enter the
        # tool loop so the model can call clarify again.
        policy_skip_parse = (
            False if clarify_failed else (skip_parse or clarified)
        )
        result = await asyncio.wait_for(
            run_llm_policy(
                session,
                text,
                tools,
                skip_parse=policy_skip_parse,
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
        ask = tools.ask_user(
            "这一轮执行时间过长，已中止。请缩小需求或点「继续」重试。",
            "turn_timeout",
        )
        result = interrupt_response(session, ask["question"], ask["reason"])
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
        ask = tools.ask_user(f"执行出错（{type(exc).__name__}），可点「继续」重试。", "agent_error")
        result = interrupt_response(session, ask["question"], ask["reason"])

    store.touch(session)
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
        else session.requirement
    )
    return await run_agent(
        session, content, skip_parse=True, checkpoint_resume=True
    )


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
    store.touch(session)
    return {
        "session_id": session.id,
        "status": session.status,
        "reply": question,
        "recommendations": session.recommendations,
        "interrupted": True,
        "interrupt_reason": reason,
    }
