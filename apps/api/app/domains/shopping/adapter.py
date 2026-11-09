"""Translate shopping sessions and generic runtime results to API shapes."""
from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from app.agent.runtime import TurnResult
from app.agent.state import AgentState
from app.config import settings
from app.schemas import MessageResponse
from app.store import Session
from app.tools import ToolBox
from app.trace import log_event


def initial_state(session: Session, turn_text: str) -> AgentState:
    messages = []
    for item in session.messages[-10:]:
        content = str(item.get("content") or "")
        if not content:
            continue
        messages.append(
            HumanMessage(content)
            if item.get("role") == "user"
            else AIMessage(content)
        )
    if (
        not messages
        or not isinstance(messages[-1], HumanMessage)
        or messages[-1].content != turn_text
    ):
        messages.append(HumanMessage(turn_text))
    return {
        "run_id": session.id,
        "messages": messages,
        "plan": None,
        "step": 0,
        "phase": "executing",
        "tool_results": [],
        "pending_user": None,
        "error": None,
    }


def to_message_response(result: TurnResult) -> MessageResponse:
    if result.status == "done":
        output = result.output or {}
        reply = str(output.get("reply") or output.get("text") or "")
        recommendations = list(output.get("recommendations") or [])
        if (
            not recommendations
            and "text" in output
            and result.steps < settings.agent_max_steps
        ):
            return MessageResponse(
                session_id=result.run_id,
                status="interrupted",
                reply=reply,
                interrupted=True,
                interrupt_reason="clarify",
            )
        return MessageResponse(
            session_id=result.run_id,
            status="done",
            reply=reply,
            recommendations=recommendations,
            interrupted=False,
            interrupt_reason=None,
        )

    pending = result.pending_user
    if result.status == "waiting_user" and pending is not None:
        clarify = pending.metadata.get("clarify") or []
        return MessageResponse(
            session_id=result.run_id,
            status="interrupted",
            reply=pending.question,
            interrupted=True,
            interrupt_reason=pending.reason,
            clarify=clarify,
        )

    if result.status == "max_steps":
        return MessageResponse(
            session_id=result.run_id,
            status="interrupted",
            reply="这一轮步骤已达上限仍未得出结论，请缩小需求或点「继续」让我接着做。",
            interrupted=True,
            interrupt_reason="step_limit",
        )

    return MessageResponse(
        session_id=result.run_id,
        status="interrupted",
        reply=f"执行出错（{result.error or result.status}），可点「继续」重试。",
        interrupted=True,
        interrupt_reason="agent_error",
    )


class ShoppingBrowser:
    def __init__(self, box: ToolBox) -> None:
        self.box = box

    async def execute(self, action: dict[str, Any]) -> dict[str, Any]:
        return await self.box._browse(
            str(action.get("tool") or "search"),
            dict(action.get("args") or {}),
        )


class ShoppingTrace:
    def __init__(self, session: Session) -> None:
        self.session = session

    def event(self, name: str, **fields: Any) -> None:
        if name == "llm.step":
            log_event(
                self.session,
                f"LLM 决策 step={fields.get('step')}: "
                f"{fields.get('tool_calls') or '文本回复'}",
                stage="llm.step",
                source="llm",
                data=fields,
            )
            return
        if name == "llm.tool":
            tool = str(fields.get("tool") or "tool")
            status = str(fields.get("status") or "ok")
            log_event(
                self.session,
                f"工具 {tool} → {status}",
                stage=f"llm.tool.{tool}",
                source="llm",
                level="warning" if status in {"error", "blocked"} else "info",
                duration_ms=fields.get("duration_ms"),
                data=fields,
            )
            return
        log_event(
            self.session,
            name,
            stage="runtime.trace",
            source="runtime",
            data=fields,
        )
