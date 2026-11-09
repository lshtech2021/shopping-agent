"""Generic planning and plan-execution helpers."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from langchain_core.messages import AIMessage, BaseMessage

from app.agent.critic import Critique
from app.agent.state import AgentState, Plan, PlanStep
from app.agent.tools import ToolResult


@runtime_checkable
class Planner(Protocol):
    async def plan(
        self, *, goal: str, context: dict[str, Any]
    ) -> Plan: ...


@runtime_checkable
class Replanner(Protocol):
    async def replan(
        self,
        *,
        state: AgentState,
        output: dict[str, Any],
        critique: Critique,
    ) -> Plan | None: ...


@runtime_checkable
class DelegatePolicy(Protocol):
    async def next_action(
        self, state: AgentState, context: list[BaseMessage]
    ) -> AIMessage: ...


class PlanExecutePolicy:
    """Mark the first open plan step before asking the model for an action."""

    def __init__(self, *, delegate: DelegatePolicy) -> None:
        self.delegate = delegate

    async def next_action(
        self, state: AgentState, context: list[BaseMessage]
    ) -> AIMessage:
        plan = plan_from_state(state)
        if plan is not None:
            step = next_open_step(plan)
            if step is not None and step.status == "pending":
                step.status = "in_progress"
                state["plan"] = plan.model_dump()
        return await self.delegate.next_action(state, context)


def plan_from_state(state: AgentState) -> Plan | None:
    raw = state.get("plan")
    if not raw:
        return None
    try:
        return Plan.model_validate(raw)
    except (TypeError, ValueError):
        return None


def next_open_step(plan: Plan) -> PlanStep | None:
    return next(
        (
            step
            for step in plan.steps
            if step.status in {"pending", "in_progress"}
        ),
        None,
    )


def first_open_step_id(plan_data: dict | None) -> str | None:
    if not plan_data:
        return None
    try:
        plan = Plan.model_validate(plan_data)
    except (TypeError, ValueError):
        return None
    step = next_open_step(plan)
    return step.id if step is not None else None


def record_plan_evidence(
    state: AgentState, result: ToolResult | dict[str, Any]
) -> None:
    """Apply generic plan evidence carried in ToolResult.meta."""
    plan = plan_from_state(state)
    if plan is None:
        return
    meta = result.meta if isinstance(result, ToolResult) else result.get("meta")
    if not isinstance(meta, dict):
        return
    step_id = str(meta.get("plan_step_id") or "")
    if not step_id:
        return
    step = next((item for item in plan.steps if item.id == step_id), None)
    if step is None:
        return

    if step.status == "pending":
        step.status = "in_progress"
    evidence = meta.get("evidence") or []
    if isinstance(evidence, str):
        evidence = [evidence]
    for item in evidence:
        text = str(item)[:200]
        if text and text not in step.evidence:
            step.evidence.append(text)
    if meta.get("plan_step_done"):
        index = plan.steps.index(step)
        if all(previous.status == "done" for previous in plan.steps[:index]):
            step.status = "done"
    state["plan"] = plan.model_dump()
