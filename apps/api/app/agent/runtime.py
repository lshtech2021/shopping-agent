"""Generic ReAct runtime driven by typed tools and control signals."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import replace
from typing import Any, Awaitable, Callable, Literal, Protocol

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from pydantic import BaseModel

from app.agent.checkpoint import CheckpointStore
from app.agent.context import ContextBuilder
from app.agent.critic import Critic, Critique
from app.agent.memory import Memory
from app.agent.policies.plan_execute import (
    Replanner,
    first_open_step_id,
    plan_from_state,
    record_plan_evidence,
)
from app.agent.state import AgentState, PendingUser, Plan
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


Finalizer = Callable[[AgentState, dict[str, Any]], Awaitable[None]]


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
        replanner: Replanner | None = None,
        max_replans: int = 2,
        finalize: Finalizer | None = None,
        checkpoint_store: CheckpointStore | None = None,
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
        self.replanner = replanner
        self.max_replans = max_replans
        self.finalize = finalize
        self.checkpoint_store = checkpoint_store
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

        replans = 0
        critic_iterations = 0
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
            llm_started = time.perf_counter()
            ai = await policy.next_action(state, context)
            llm_duration_ms = (time.perf_counter() - llm_started) * 1000
            state["messages"].append(ai)
            state["step"] = int(state["step"]) + 1
            tool_calls = list(getattr(ai, "tool_calls", None) or [])
            usage = getattr(ai, "usage_metadata", None)
            if not isinstance(usage, dict):
                usage = {}
            response_metadata = getattr(ai, "response_metadata", None)
            if not isinstance(response_metadata, dict):
                response_metadata = {}
            self.tool_context.trace.event(
                "llm.call",
                step=int(state["step"]),
                duration_ms=llm_duration_ms,
                model=response_metadata.get("model_name")
                or response_metadata.get("model"),
                prompt_tokens=usage.get("input_tokens"),
                completion_tokens=usage.get("output_tokens"),
                total_tokens=usage.get("total_tokens"),
                finish_reason=response_metadata.get("finish_reason"),
                tool_calls=[call.get("name") for call in tool_calls],
            )
            self.tool_context.trace.event(
                "llm.step",
                step=int(state["step"]),
                tool_calls=[call.get("name") for call in tool_calls],
                text=_text_of(ai)[:200],
            )
            if not tool_calls:
                state["phase"] = "done"
                await self._clear_checkpoint(run_id)
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
            batch_step_id = first_open_step_id(state.get("plan"))
            executed = await self._execute_tool_batch(
                tool_calls, state, run_id, batch_step_id
            )
            for call, result, started in executed:
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
                if result.meta.get("plan_step_id"):
                    self.tool_context.trace.event(
                        "plan.step",
                        plan=state.get("plan"),
                        step_id=result.meta.get("plan_step_id"),
                        tool=result.tool,
                    )
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
            await self._save_checkpoint(state)
            current_plan = plan_from_state(state)
            persisted_replans = (
                int((current_plan.metadata or {}).get("replan_count") or 0)
                if current_plan is not None
                else 0
            )
            if (
                terminal is None
                and self.replanner is not None
                and max(replans, persisted_replans) < self.max_replans
            ):
                results = state.get("tool_results") or []
                latest_output = (
                    results[-1].get("data")
                    if results and isinstance(results[-1], dict)
                    else {}
                ) or {}
                new_plan = await self.replanner.replan(
                    state=state,
                    output=latest_output,
                    critique=Critique(ok=True),
                )
                if isinstance(new_plan, Plan):
                    state["plan"] = new_plan.model_dump()
                    replans = max(
                        replans + 1,
                        int((new_plan.metadata or {}).get("replan_count") or 0),
                    )
                    self.tool_context.trace.event(
                        "plan.replanned",
                        plan=new_plan.model_dump(),
                        replan_count=replans,
                    )
                    await self._save_checkpoint(state)
                    continue

            if waiting is not None:
                state["phase"] = "waiting_user"
                state["pending_user"] = waiting.model_dump()
                await self._save_checkpoint(state)
                return TurnResult(
                    run_id=run_id,
                    status="waiting_user",
                    pending_user=waiting,
                    steps=int(state["step"]),
                )
            if failure is not None:
                state["phase"] = "failed"
                state["error"] = failure
                await self._save_checkpoint(state)
                return TurnResult(
                    run_id=run_id,
                    status="failed",
                    error=failure,
                    steps=int(state["step"]),
                )
            if finish_output is not None:
                critique: Critique | None = None
                if self.critic is not None:
                    try:
                        critique = await self.critic.evaluate(
                            state=state, output=finish_output
                        )
                        self.tool_context.trace.event(
                            "critic.evaluated",
                            step=int(state["step"]),
                            okay=critique.ok,
                            violations=critique.violations,
                            scores=critique.scores,
                        )
                    except Exception as exc:  # noqa: BLE001 — advisory
                        self.tool_context.trace.event(
                            "critic.error",
                            step=int(state["step"]),
                            error=type(exc).__name__,
                            detail=str(exc),
                        )
                        critique = None
                if critique is not None and not critique.ok:
                    if (
                        critique.retry
                        and critic_iterations < self.max_critic_iterations
                    ):
                        critic_iterations += 1
                        state["messages"].append(
                            HumanMessage(
                                content="[CRITIC_FEEDBACK]\n"
                                + json.dumps(
                                    critique.model_dump(),
                                    ensure_ascii=False,
                                    default=str,
                                )
                            )
                        )
                        self.tool_context.trace.event(
                            "critic.retry",
                            step=int(state["step"]),
                            critique=critique.model_dump(),
                        )
                        continue
                    self.tool_context.trace.event(
                        "critic.rejected",
                        step=int(state["step"]),
                        violations=critique.violations,
                        retry=critique.retry,
                    )
                    state["phase"] = "failed"
                    state["error"] = "critic_rejected"
                    return TurnResult(
                        run_id=run_id,
                        status="failed",
                        output=finish_output,
                        error="critic_rejected",
                        steps=int(state["step"]),
                    )
                if self.finalize is not None:
                    try:
                        await self.finalize(state, finish_output)
                    except Exception as exc:  # noqa: BLE001
                        state["phase"] = "failed"
                        state["error"] = f"finalize_failed: {exc}"
                        return TurnResult(
                            run_id=run_id,
                            status="failed",
                            output=finish_output,
                            error=f"finalize_failed: {exc}",
                            steps=int(state["step"]),
                        )
                state["phase"] = "done"
                await self._clear_checkpoint(run_id)
                return TurnResult(
                    run_id=run_id,
                    status="done",
                    output=finish_output,
                    steps=int(state["step"]),
                )

        state["phase"] = "done"
        await self._clear_checkpoint(run_id)
        return TurnResult(
            run_id=run_id,
            status="max_steps",
            steps=int(state["step"]),
        )

    async def _save_checkpoint(self, state: AgentState) -> None:
        if self.checkpoint_store is None:
            return
        run_id = str(state.get("run_id") or "")
        if not run_id:
            return
        try:
            await self.checkpoint_store.save(run_id, state)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001 — checkpointing is best effort
            return None

    async def _clear_checkpoint(self, run_id: str) -> None:
        if self.checkpoint_store is None or not run_id:
            return
        try:
            await self.checkpoint_store.delete(run_id)
        except Exception:  # noqa: BLE001
            return None

    async def resume_from_checkpoint(
        self,
        *,
        run_id: str,
        policy: Policy | None = None,
        user_input: str | None = None,
    ) -> TurnResult | None:
        if self.checkpoint_store is None:
            return None
        state = await self.checkpoint_store.load(run_id)
        if state is None:
            return None
        if user_input is not None:
            state["messages"].append(HumanMessage(content=user_input))
            state["pending_user"] = None
            state["phase"] = "executing"
        chosen = policy or self._last_policy
        if chosen is None:
            return TurnResult(
                run_id=run_id,
                status="failed",
                error="policy_required",
                steps=int(state.get("step") or 0),
            )
        return await self.run_turn(state=state, policy=chosen)  # type: ignore[arg-type]

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

    def _batch_is_parallel_safe(self, tool_calls: list[dict[str, Any]]) -> bool:
        if len(tool_calls) < 2:
            return False
        for call in tool_calls:
            name = str(call.get("name") or "")
            spec = self.tools.get(name)
            if spec is None or not spec.parallel_safe:
                return False
        return True

    async def _execute_tool_batch(
        self,
        tool_calls: list[dict[str, Any]],
        state: AgentState,
        run_id: str,
        step_id: str | None,
    ) -> list[tuple[dict[str, Any], ToolResult, float]]:
        if self._batch_is_parallel_safe(tool_calls):

            async def _one(
                call: dict[str, Any],
            ) -> tuple[dict[str, Any], ToolResult, float]:
                started = time.perf_counter()
                result = await self._execute_tool(
                    call, state, run_id, step_id
                )
                return call, result, started

            return list(
                await asyncio.gather(*[_one(call) for call in tool_calls])
            )

        executed: list[tuple[dict[str, Any], ToolResult, float]] = []
        terminal = False
        for call in tool_calls:
            started = time.perf_counter()
            if terminal:
                # The previous call already finished/waiting for the user.
                # Keep the protocol complete without reporting a failure for
                # work that was intentionally not executed.
                result = ToolResult(
                    call_id=str(call.get("id") or uuid.uuid4().hex),
                    tool=str(call.get("name") or ""),
                    ok=True,
                    data={
                        "status": "skipped",
                        "skipped": True,
                        "reason": "turn_already_finished",
                    },
                )
            else:
                result = await self._execute_tool(
                    call, state, run_id, step_id
                )
                if result.control in {"finish", "wait_user", "fail"}:
                    terminal = True
            executed.append((call, result, started))
        return executed

    async def _execute_tool(
        self,
        call: dict[str, Any],
        state: AgentState,
        run_id: str,
        step_id: str | None = None,
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
        resources.pop("plan_step_id", None)
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
