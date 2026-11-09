"""Agent entrypoint: one turn per user message / resume.

Policy is always the LangGraph LLM tool loop (``llm_agent``). Hard guarantees
(quotas, risk-site lockout, recommend id validation) live in ``ToolBox``.
"""
from __future__ import annotations

import logging
from typing import Any

from app import llm
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
) -> dict[str, Any]:
    session.turn += 1
    session.status = "running"
    session.interrupt_reason = None
    session.quotas = {}  # design §6: quotas are per turn, not per session
    tools = ToolBox(session)
    text = (content or session.requirement or "").strip()
    clarified = False
    if answers:
        if session.pending_questions:
            tools.apply_clarify(answers)
            clarified = True  # answers are merged; don't re-parse raw text over them
        else:
            log_event(
                session,
                "收到澄清回答但没有待答问题，已忽略",
                stage="agent.clarify",
                source="agent",
                level="warning",
                data={"answers": answers},
            )
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

        result = await run_llm_policy(
            session, text, tools, skip_parse=skip_parse or clarified
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
    return await run_agent(session, content, skip_parse=True)


def blocked_text(site: str, result: dict[str, Any], what: str) -> str:
    status = str(result.get("status"))
    error = result.get("error")
    if error == "no_extension_online":
        return "没有检测到在线的浏览器插件。请打开 Chrome 并确认 Side Panel 显示 WS: connected 后点「继续」。"
    if error == "extension_disconnected":
        return "插件连接中断，请确认插件在线后点「继续」。"
    if error == "job_timeout":
        return f"{site} {what}超时（页面可能卡住或需要验证），处理后点「继续」。"
    if status in {"rate_limited", "captcha", "login_required"}:
        label = {"rate_limited": "访问过频", "captcha": "出现验证码", "login_required": "需要登录"}[status]
        return f"{site} {what}受阻：{label}。请在浏览器中处理后点「继续」。"
    return f"{site} {what}受阻（{error or status}），处理后点「继续」。"


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
