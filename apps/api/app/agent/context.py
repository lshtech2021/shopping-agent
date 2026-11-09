"""Build the model context under a coarse character/token budget."""
from __future__ import annotations

import json

from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from app.agent.state import AgentState
from app.agent.tools import ToolSpec

UNTRUSTED_PAGE_PREFIX = "[UNTRUSTED_PAGE_DATA]\n"
SUMMARIZED_TOOL_PREFIX = "[tool result summarized: "
TRUNCATED_SUFFIX = "\n[truncated]"


def _estimate_tokens(text: str) -> int:
    # Deliberately simple and provider-independent: ~4 chars per token.
    return max(1, (len(text) + 3) // 4)


def _content_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, default=str)


class ContextBuilder:
    def __init__(self, max_tokens: int = 12000) -> None:
        self.max_tokens = max_tokens

    def build(
        self,
        *,
        system_prompt: str,
        state: AgentState,
        memory_summary: str,
        tool_specs: list[ToolSpec],
    ) -> list[BaseMessage]:
        system_text = self._system_text(
            system_prompt, state, memory_summary, tool_specs
        )
        messages: list[BaseMessage] = list(state.get("messages") or [])[-10:]

        pending = self._pending_tool_result(state, messages)
        if pending is not None:
            messages.append(pending)

        messages = [self._mark_untrusted(message) for message in messages]

        max_chars = max(1, self.max_tokens * 4)
        system_text = self._truncate(system_text, max_chars)
        messages = self._summarize_old_tools(messages)
        per_message_cap = max(64, max_chars // 2)
        messages = [
            self._truncate_message(message, per_message_cap)
            for message in messages
        ]

        while (
            len(system_text) + sum(len(_content_text(m)) for m in messages)
            > max_chars
            and len(messages) > 1
        ):
            messages.pop(0)

        if messages:
            remaining = max_chars - len(system_text)
            messages = self._fit_messages(messages, remaining)

        return [SystemMessage(content=system_text), *messages]

    def _system_text(
        self,
        system_prompt: str,
        state: AgentState,
        memory_summary: str,
        tool_specs: list[ToolSpec],
    ) -> str:
        summary = {
            key: state.get(key)
            for key in (
                "run_id",
                "phase",
                "step",
                "plan",
                "pending_user",
                "error",
            )
        }
        parts = [system_prompt]
        if memory_summary:
            parts.append(f"## Memory Summary\n{memory_summary}")
        parts.append(
            "## Runtime State\n"
            + json.dumps(summary, ensure_ascii=False, default=str)
        )
        if tool_specs:
            tools = "\n".join(
                f"- {spec.name}: {spec.description}" for spec in tool_specs
            )
            parts.append(f"## Available Tools\n{tools}")
        return "\n\n".join(parts)

    def _pending_tool_result(
        self, state: AgentState, messages: list[BaseMessage]
    ) -> HumanMessage | None:
        results = state.get("tool_results") or []
        if not results:
            return None
        last = results[-1]
        if not isinstance(last, dict) or last.get("control", "continue") != "continue":
            return None
        call_id = str(last.get("call_id") or "")
        if call_id and any(
            isinstance(message, ToolMessage)
            and message.tool_call_id == call_id
            for message in messages
        ):
            return None
        return HumanMessage(
            content=UNTRUSTED_PAGE_PREFIX
            + json.dumps(last, ensure_ascii=False, default=str)
        )

    def _mark_untrusted(self, message: BaseMessage) -> BaseMessage:
        if not isinstance(message, ToolMessage):
            return message
        text = _content_text(message)
        if text.startswith("[UNTRUSTED_TOOL_RESULT]") or text.startswith(
            UNTRUSTED_PAGE_PREFIX
        ):
            return message
        return message.model_copy(
            update={"content": UNTRUSTED_PAGE_PREFIX + text}
        )

    def _summarize_old_tools(
        self, messages: list[BaseMessage]
    ) -> list[BaseMessage]:
        for index, message in enumerate(messages):
            if not isinstance(message, ToolMessage):
                continue
            if index == len(messages) - 1:
                continue
            messages[index] = message.model_copy(
                update={
                    "content": SUMMARIZED_TOOL_PREFIX
                    + f"{message.name or 'tool'}]"
                }
            )
        return messages

    def _fit_messages(
        self, messages: list[BaseMessage], remaining: int
    ) -> list[BaseMessage]:
        if remaining <= 0:
            return messages[-1:]
        per_message = max(16, remaining // len(messages))
        fitted: list[BaseMessage] = []
        used = 0
        for message in messages:
            text = _content_text(message)
            allowed = min(per_message, remaining - used)
            if allowed <= len(TRUNCATED_SUFFIX) + 8:
                continue
            if len(text) > allowed:
                text = text[: allowed - len(TRUNCATED_SUFFIX)] + TRUNCATED_SUFFIX
            fitted.append(message.model_copy(update={"content": text}))
            used += len(text)
            if used >= remaining:
                break
        return fitted

    @staticmethod
    def _truncate_message(
        message: BaseMessage, max_chars: int
    ) -> BaseMessage:
        text = _content_text(message)
        if len(text) <= max_chars:
            return message
        return message.model_copy(
            update={
                "content": text[: max_chars - len(TRUNCATED_SUFFIX)]
                + TRUNCATED_SUFFIX
            }
        )

    @staticmethod
    def _truncate(text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[: max(0, max_chars - len(TRUNCATED_SUFFIX))] + TRUNCATED_SUFFIX


__all__ = ["ContextBuilder", "UNTRUSTED_PAGE_PREFIX"]
