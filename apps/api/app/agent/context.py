"""Build a valid, budgeted model context."""
from __future__ import annotations

import json
from typing import Any, Iterable

from langchain_core.messages import (
    AIMessage,
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
    """Conservative estimate: one token per CJK glyph, ~4 chars elsewhere."""
    cjk = sum(1 for char in text if ord(char) >= 0x2E80)
    other = len(text) - cjk
    return max(1, cjk + (other + 3) // 4)


def _content_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, default=str)


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    if _estimate_tokens(text) <= max_tokens:
        return text
    if max_tokens <= _estimate_tokens(TRUNCATED_SUFFIX):
        return text[: max(1, max_tokens)]
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if _estimate_tokens(text[:middle] + TRUNCATED_SUFFIX) <= max_tokens:
            low = middle
        else:
            high = middle - 1
    return text[:low] + TRUNCATED_SUFFIX


class ContextBuilder:
    def __init__(self, max_tokens: int = 12000) -> None:
        self.max_tokens = max(8, max_tokens)

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
        if _estimate_tokens(system_text) > self.max_tokens // 2:
            system_text = _truncate_to_tokens(
                system_text, max(1, self.max_tokens // 2)
            )
        system_tokens = _estimate_tokens(system_text)
        budget = max(1, self.max_tokens - system_tokens)

        units, current_index = self._units(state)
        units = units[-10:]
        current_index = min(current_index, len(units) - 1)
        units = self._fill_missing_results(units)
        units = [
            [self._mark_untrusted(message) for message in unit]
            for unit in units
        ]
        latest_human = self._latest_human(units)

        if self._total_tokens(units) > budget:
            self._summarize_units(units, self._summary_stop(units))

        protected = {current_index}
        if latest_human is not None:
            for index, unit in enumerate(units):
                if any(message is latest_human for message in unit):
                    protected.add(index)
        while self._total_tokens(units) > budget and len(units) > 1:
            candidates = [
                index for index in range(len(units)) if index not in protected
            ]
            if not candidates:
                break
            drop = candidates[0]
            units.pop(drop)
            current_index = self._shift(current_index, drop)
            protected = {self._shift(index, drop) for index in protected}
            protected.discard(-1)
            protected.add(current_index)

        units = self._shrink(units, budget, latest_human)
        messages = [message for unit in units for message in unit]
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

    def _units(self, state: AgentState) -> tuple[list[list[BaseMessage]], int]:
        messages = list(state.get("messages") or [])
        pending = self._pending_payload(state, messages)
        units = self._group(messages)
        current_index = len(units) - 1
        if pending is not None:
            payload = UNTRUSTED_PAGE_PREFIX + json.dumps(
                pending, ensure_ascii=False, default=str
            )
            call_id = str(pending.get("call_id") or "")
            attached = False
            for index, unit in enumerate(units):
                first = unit[0]
                calls = getattr(first, "tool_calls", None) if isinstance(
                    first, AIMessage
                ) else None
                if calls and any(call.get("id") == call_id for call in calls):
                    unit.append(
                        ToolMessage(
                            content=payload,
                            tool_call_id=call_id,
                            name=str(pending.get("tool") or "tool"),
                        )
                    )
                    current_index = index
                    attached = True
                    break
            if not attached:
                units.append([HumanMessage(content=payload)])
                current_index = max(0, len(units) - 2)
        return units, current_index

    def _group(self, messages: Iterable[BaseMessage]) -> list[list[BaseMessage]]:
        units: list[list[BaseMessage]] = []
        open_ids: set[str] = set()
        open_unit: list[BaseMessage] | None = None
        for message in messages:
            if isinstance(message, AIMessage) and getattr(
                message, "tool_calls", None
            ):
                open_unit = [message]
                open_ids = {
                    str(call.get("id")) for call in message.tool_calls
                }
                units.append(open_unit)
                continue
            if isinstance(message, ToolMessage):
                if open_unit is not None and message.tool_call_id in open_ids:
                    open_unit.append(message)
                    open_ids.discard(message.tool_call_id)
                # A tool message without its assistant call is not valid input.
                continue
            open_unit = None
            open_ids = set()
            units.append([message])
        return units

    def _pending_payload(
        self, state: AgentState, messages: list[BaseMessage]
    ) -> dict[str, Any] | None:
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
        return last

    def _fill_missing_results(
        self, units: list[list[BaseMessage]]
    ) -> list[list[BaseMessage]]:
        for unit in units:
            first = unit[0]
            if not isinstance(first, AIMessage) or not getattr(
                first, "tool_calls", None
            ):
                continue
            present = {
                message.tool_call_id
                for message in unit
                if isinstance(message, ToolMessage)
            }
            for call in first.tool_calls:
                call_id = str(call.get("id"))
                if call_id in present:
                    continue
                unit.append(
                    ToolMessage(
                        content=UNTRUSTED_PAGE_PREFIX
                        + json.dumps(
                            {"error": "missing_tool_result"},
                            ensure_ascii=False,
                        ),
                        tool_call_id=call_id,
                        name=str(call.get("name") or "tool"),
                    )
                )
        return units

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

    def _summary_stop(self, units: list[list[BaseMessage]]) -> int:
        last_tool = -1
        for index, unit in enumerate(units):
            first = unit[0]
            if isinstance(first, AIMessage) and getattr(
                first, "tool_calls", None
            ):
                last_tool = index
        if last_tool < 0:
            return len(units)
        later = [
            message for unit in units[last_tool + 1 :] for message in unit
        ]
        if any(isinstance(message, HumanMessage) for message in later):
            return len(units)
        return last_tool

    def _summarize_units(
        self, units: list[list[BaseMessage]], stop: int
    ) -> None:
        for unit in units[:stop]:
            for index, message in enumerate(unit):
                if isinstance(message, ToolMessage):
                    unit[index] = message.model_copy(
                        update={
                            "content": SUMMARIZED_TOOL_PREFIX
                            + f"{message.name or 'tool'}]"
                        }
                    )

    def _shrink(
        self,
        units: list[list[BaseMessage]],
        budget: int,
        latest_human: HumanMessage | None,
    ) -> list[list[BaseMessage]]:
        while self._total_tokens(units) > budget:
            candidates: list[tuple[int, int, int]] = []
            for unit_index, unit in enumerate(units):
                for message_index, message in enumerate(unit):
                    if message is latest_human:
                        continue
                    tokens = _estimate_tokens(_content_text(message))
                    if tokens > 1:
                        candidates.append(
                            (tokens, unit_index, message_index)
                        )
            if not candidates:
                break
            tokens, unit_index, message_index = max(candidates)
            message = units[unit_index][message_index]
            excess = self._total_tokens(units) - budget
            allowed = max(1, tokens - excess)
            new_text = _truncate_to_tokens(_content_text(message), allowed)
            if new_text == _content_text(message):
                new_text = _truncate_to_tokens(
                    _content_text(message), max(1, tokens // 2)
                )
            units[unit_index][message_index] = message.model_copy(
                update={"content": new_text}
            )

        if latest_human is not None:
            total = self._total_tokens(units)
            if total > budget:
                other = total - _estimate_tokens(
                    _content_text(latest_human)
                )
                allowed = max(1, budget - other)
                text = _truncate_to_tokens(
                    _content_text(latest_human), allowed
                )
                for unit in units:
                    for index, message in enumerate(unit):
                        if message is latest_human:
                            unit[index] = message.model_copy(
                                update={"content": text}
                            )
        return units

    @staticmethod
    def _total_tokens(units: list[list[BaseMessage]]) -> int:
        return sum(
            _estimate_tokens(_content_text(message))
            for unit in units
            for message in unit
        )

    @staticmethod
    def _latest_human(
        units: list[list[BaseMessage]],
    ) -> HumanMessage | None:
        for unit in reversed(units):
            for message in reversed(unit):
                if isinstance(message, HumanMessage):
                    return message
        return None

    @staticmethod
    def _shift(index: int, drop: int) -> int:
        return index - 1 if index > drop else index


__all__ = ["ContextBuilder", "UNTRUSTED_PAGE_PREFIX"]
