"""Translate shopping sessions and generic runtime results to API shapes."""
from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from app.agent.runtime import TurnResult
from app.agent.state import AgentState
from app.config import settings
from app.schemas import MessageResponse
from app.store import Session, store
from app.tools import ToolBox
from app.trace import classify_error, log_event


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
        "plan": session.plan,
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
        clarify = pending.metadata.get("questions") or []
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

    if result.error == "critic_rejected":
        return MessageResponse(
            session_id=result.run_id,
            status="interrupted",
            reply="候选没有通过质量校验，请调整需求或点「继续」让我重新选品。",
            interrupted=True,
            interrupt_reason="critic_rejected",
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
        return await self.box.dispatch_browse(
            str(action.get("tool") or "search"),
            dict(action.get("args") or {}),
        )


def _payload_chars(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return 0


def _tool_result_summary(result: Any) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    summary: dict[str, Any] = {}
    for key in (
        "status",
        "error",
        "accepted",
        "product_count",
        "result_count",
        "page_item_count",
        "fallback_item_count",
        "count",
        "total_cached",
        "item_count",
        "page",
    ):
        if key in result:
            summary[key] = result[key]
    if isinstance(result.get("items"), list):
        summary["items"] = len(result["items"])
    if isinstance(result.get("excluded"), dict):
        summary["excluded"] = result["excluded"]
    if isinstance(result.get("excluded_primary"), dict):
        summary["excluded_primary"] = result["excluded_primary"]
    if isinstance(result.get("extension_errors"), list):
        summary["extension_errors"] = result["extension_errors"][-3:]
    product = result.get("product")
    if isinstance(product, dict):
        summary["product"] = {
            key: product.get(key)
            for key in (
                "product_id",
                "title",
                "price",
                "currency",
                "shipping_cost",
                "landed_cost",
                "cost_complete",
            )
        }
    return summary or None


class ShoppingTrace:
    def __init__(self, session: Session) -> None:
        self.session = session

    def event(self, name: str, **fields: Any) -> None:
        if name == "llm.call":
            log_event(
                self.session,
                "LLM 调用",
                stage="llm.call",
                source="llm",
                duration_ms=fields.get("duration_ms"),
                data=fields,
            )
            return
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
            result = fields.get("result")
            summary = _tool_result_summary(result)
            if result is not None and settings.artifact_storage_enabled:
                payload_chars = _payload_chars(result)
                if payload_chars >= settings.artifact_threshold_chars:
                    fields["payload_ref"] = store.save_artifact(
                        self.session.id,
                        f"tool.{tool}",
                        result,
                    )
                    fields["payload_chars"] = payload_chars
                    fields["result"] = summary
            fields["result_summary"] = summary
            fields["error_code"] = classify_error(
                status,
                fields.get("error"),
                fields.get("result") if isinstance(fields.get("result"), dict) else {},
            )
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
        if name.startswith("plan.") or name.startswith("critic."):
            log_event(
                self.session,
                f"计划/评审事件：{name}",
                stage=name,
                source="agent",
                level=(
                    "error"
                    if name == "critic.error"
                    else "warning"
                    if name == "critic.rejected"
                    else "info"
                ),
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
