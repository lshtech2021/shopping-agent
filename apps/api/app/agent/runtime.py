"""Generic ReAct runtime driven by typed tools and control signals."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import replace
from typing import Any, Literal, Protocol

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from pydantic import BaseModel

from app.agent.context import ContextBuilder
from app.agent.critic import Critic
from app.agent.memory import Memory
from app.agent.policies.plan_execute import (
    first_open_step_id,
    record_plan_evidence,
)
from app.agent.state import AgentState, PendingUser
from app.agent.tools import (
    BrowserClient,
    ToolContext,
    ToolRegistry,
    ToolResult,
    Tracer,
)


class TurnResult(BaseModel):
    run_id: str
    status: Literal["done", "waiting_user", "failed", "max_steps", "timeout"]
    output: dict[str, Any] | None = None
    pending_user: PendingUser | None = None
    error: str | None = None
    steps: int = 0


class Policy(Protocol):
    async def next_action(
        self, state: AgentState, context: list[BaseMessage]
    ) -> AIMessage: ...


class _NullMemory:
    async def load(self, run_id: str) -> dict[str, Any]:
        return {}

    async def save(self, run_id: str, patch: dict[str, Any]) -> None:
        return None

    async def query(
        self, query: str, *, limit: int
    ) -> list[dict[str, Any]]:
        return []

    async def summarize(self, run_id: str) -> str:
        return ""


class _NullBrowser:
    async def execute(self, action: dict[str, Any]) -> dict[str, Any]:
        return {}


class _NullTracer:
    def event(self, name: str, **fields: Any) -> None:
        return None


def _text_of(message: AIMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content.strip()
    return " ".join(
        part.get("text", "") if isinstance(part, dict) else str(part)
        for part in content
    ).strip()


def _pending_user(output: dict[str, Any] | None) -> PendingUser:
    output = output or {}
    raw = output.get("pending_user")
    if isinstance(raw, dict):
        return PendingUser(**raw)
    return PendingUser(
        question=str(output.get("question") or output.get("reply") or ""),
        reason=str(output.get("reason") or "wait_user"),
        options=list(output.get("options") or []),
        metadata=dict(output.get("metadata") or {}),
    )


class AgentRuntime:
    def __init__(
        self,
        *,
        tools: ToolRegistry,
        context_builder: ContextBuilder,
        critic: Critic | None = None,
        max_steps: int = 24,
        max_critic_iterations: int = 2,
        system_prompt: str = "",
        memory: Memory | None = None,
        tool_context: ToolContext | None = None,
        memory_summary: str = "",
    ) -> None:
        self.tools = tools
        self.context_builder = context_builder
        self.critic = critic
        self.max_steps = max_steps
        self.max_critic_iterations = max_critic_iterations
        self.system_prompt = system_prompt
        self.memory = memory or _NullMemory()
        self.tool_context = tool_context or ToolContext(
            run_id="",
            memory=self.memory,
            browser=_NullBrowser(),
            trace=_NullTracer(),
        )
        self.memory_summary = memory_summary
        self._last_policy: Policy | None = None

    async def run_turn(
        self, *, state: AgentState, policy: Policy
    ) -> TurnResult:
        self._last_policy = policy
        run_id = str(state.get("run_id") or "")
        state.setdefault("messages", [])
        state["phase"] = "executing"
        state.setdefault("tool_results", [])
        state.setdefault("step", 0)
        state.setdefault("plan", None)
        state.setdefault("pending_user", None)
        state.setdefault("error", None)

        while int(state["step"]) < self.max_steps:
            memory_summary = self.memory_summary
            if self.memory is not None:
                try:
                    memory_summary = await self.memory.summarize(run_id)
                except Exception:  # noqa: BLE001 — memory is advisory context
                    memory_summary = self.memory_summary
            context = self.context_builder.build(
                system_prompt=self.system_prompt,
                state=state,
                memory_summary=memory_summary,
                tool_specs=self.tools.specs(),
            )
            ai = await policy.next_action(state, context)
            state["messages"].append(ai)
            state["step"] = int(state["step"]) + 1
            tool_calls = list(getattr(ai, "tool_calls", None) or [])
            self.tool_context.trace.event(
                "llm.step",
                step=int(state["step"]),
                tool_calls=[call.get("name") for call in tool_calls],
                text=_text_of(ai)[:200],
            )
            if not tool_calls:
                state["phase"] = "done"
                return TurnResult(
                    run_id=run_id,
                    status="done",
                    output={"text": _text_of(ai)},
                    steps=int(state["step"]),
                )

            finish_output: dict[str, Any] | None = None
            waiting: PendingUser | None = None
            failure: str | None = None
            terminal: str | None = None
            for call in tool_calls:
                started = time.perf_counter()
                if terminal is not None:
                    result = ToolResult(
                        call_id=str(call.get("id") or uuid.uuid4().hex),
                        tool=str(call.get("name") or ""),
                        ok=False,
                        data={"error": "turn_already_finished"},
                        error="turn_already_finished",
                    )
                else:
                    result = await self._execute_tool(call, state, run_id)
                self.tool_context.trace.event(
                    "llm.tool",
                    tool=result.tool,
                    args=call.get("args") or {},
                    result=result.data,
                    error=result.error,
                    status=result.data.get("status")
                    or ("error" if not result.ok else "ok"),
                    duration_ms=(time.perf_counter() - started) * 1000,
                )
                state["tool_results"].append(result.model_dump())
                record_plan_evidence(state, result)
                payload = dict(result.data)
                if result.error:
                    payload.setdefault("error", result.error)
                if result.control != "continue":
                    payload.setdefault("control", result.control)
                if result.output is not None:
                    payload.setdefault("output", result.output)
                state["messages"].append(
                    ToolMessage(
                        content="[UNTRUSTED_TOOL_RESULT]\n"
                        + json.dumps(
                            payload,
                            ensure_ascii=False,
                            default=str,
                        ),
                        tool_call_id=result.call_id,
                        name=result.tool,
                    )
                )
                if terminal is not None:
                    continue
                if result.control == "finish":
                    finish_output = result.output or {}
                    terminal = "finish"
                elif result.control == "wait_user":
                    waiting = _pending_user(result.output)
                    terminal = "wait_user"
                elif result.control == "fail":
                    failure = result.error or "tool_failed"
                    terminal = "fail"

            if waiting is not None:
                state["phase"] = "waiting_user"
                state["pending_user"] = waiting.model_dump()
                return TurnResult(
                    run_id=run_id,
                    status="waiting_user",
                    pending_user=waiting,
                    steps=int(state["step"]),
                )
            if failure is not None:
                state["phase"] = "failed"
                state["error"] = failure
                return TurnResult(
                    run_id=run_id,
                    status="failed",
                    error=failure,
                    steps=int(state["step"]),
                )
            if finish_output is not None:
                state["phase"] = "done"
                return TurnResult(
                    run_id=run_id,
                    status="done",
                    output=finish_output,
                    steps=int(state["step"]),
                )

        state["phase"] = "done"
        return TurnResult(
            run_id=run_id,
            status="max_steps",
            steps=int(state["step"]),
        )

    async def resume(
        self,
        *,
        state: AgentState,
        user_input: str,
        policy: Policy | None = None,
    ) -> TurnResult:
        state.setdefault("messages", [])
        state["messages"].append(HumanMessage(content=user_input))
        state["pending_user"] = None
        state["phase"] = "executing"
        chosen = policy or self._last_policy
        if chosen is None:
            return TurnResult(
                run_id=str(state.get("run_id") or ""),
                status="failed",
                error="policy_required",
                steps=int(state.get("step") or 0),
            )
        return await self.run_turn(state=state, policy=chosen)

    async def _execute_tool(
        self, call: dict[str, Any], state: AgentState, run_id: str
    ) -> ToolResult:
        name = str(call.get("name") or "")
        call_id = str(call.get("id") or uuid.uuid4().hex)
        spec = self.tools.get(name)
        if spec is None:
            return ToolResult(
                call_id=call_id,
                tool=name,
                ok=False,
                error=f"unknown_tool:{name}",
            )
        resources = dict(self.tool_context.resources)
        step_id = first_open_step_id(state.get("plan"))
        if step_id:
            resources["plan_step_id"] = step_id
        context = replace(
            self.tool_context, run_id=run_id, resources=resources
        )
        try:
            args = spec.args_model.model_validate(call.get("args") or {})
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                call_id=call_id,
                tool=name,
                ok=False,
                error=f"invalid_args:{type(exc).__name__}: {exc}",
            )
        try:
            result = await spec.handler(args, context)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                call_id=call_id,
                tool=name,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
            )
        if not isinstance(result, ToolResult):
            return ToolResult(
                call_id=call_id,
                tool=name,
                ok=False,
                error="invalid_tool_result",
            )
        return result.model_copy(
            update={"call_id": call_id, "tool": name}
        )
