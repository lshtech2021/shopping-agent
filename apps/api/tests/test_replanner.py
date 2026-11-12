from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from langchain_core.messages import AIMessage, SystemMessage
from pydantic import BaseModel

from app.agent.critic import Critique
from app.agent.policies.plan_execute import Replanner
from app.agent.runtime import AgentRuntime
from app.agent.state import AgentState, Plan, PlanStep
from app.agent.tools import ToolRegistry, ToolResult, ToolSpec
from app.domains.shopping.planner import ShoppingReplanner


class EmptyArgs(BaseModel):
    pass


def _state(poor: bool = True) -> AgentState:
    plan = Plan(
        goal="buy a laptop",
        steps=[PlanStep(id="search", description="search")],
        metadata={"search_queries": {"jd": ["开发笔记本"]}},
    )
    return {
        "run_id": "run-1",
        "messages": [],
        "plan": plan.model_dump(),
        "step": 0,
        "phase": "executing",
        "tool_results": [
            {
                "call_id": "c1",
                "tool": "search",
                "ok": True,
                "data": {"quality": "poor" if poor else "good", "result_count": 0 if poor else 10},
                "meta": {},
            }
        ],
        "pending_user": None,
        "error": None,
    }


def test_generic_replanner_protocol_accepts_shopping_replanner():
    assert isinstance(ShoppingReplanner(), Replanner)


def test_shopping_replanner_changes_queries_on_poor_result():
    state = _state(poor=True)
    plan = asyncio.run(
        ShoppingReplanner().replan(
            state=state,
            output={},
            critique=Critique(ok=True),
        )
    )

    assert plan is not None
    assert plan.metadata["search_queries"] == {}
    assert plan.metadata["replan_count"] == 1
    assert plan.metadata["replan_reason"] == "poor_result"
    assert [step.id for step in plan.steps] == ["search"]
    assert plan.steps[0].status == "in_progress"


def test_shopping_replanner_drops_queries_for_all_sites_without_suffixes():
    state = _state(poor=True)
    plan = Plan.model_validate(state["plan"])
    metadata = dict(plan.metadata)
    metadata["search_queries"] = {
        "jd": ["开发笔记本"],
        "ebay": ["laptop"],
    }
    state["plan"] = plan.model_copy(update={"metadata": metadata}).model_dump()

    adjusted = asyncio.run(
        ShoppingReplanner().replan(
            state=state, output={}, critique=Critique(ok=True)
        )
    )

    assert adjusted is not None
    assert adjusted.metadata["search_queries"] == {}


def test_shopping_replanner_does_not_invent_a_used_exclusion():
    state = _state(poor=True)
    plan = Plan.model_validate(state["plan"])
    metadata = dict(plan.metadata)
    metadata["search_queries"] = {"ebay": ["laptop"]}
    state["plan"] = plan.model_copy(update={"metadata": metadata}).model_dump()

    first = asyncio.run(
        ShoppingReplanner().replan(
            state=state, output={}, critique=Critique(ok=True)
        )
    )

    assert first is not None
    assert first.metadata["search_queries"] == {}

    state["plan"] = first.model_dump()
    second = asyncio.run(
        ShoppingReplanner().replan(
            state=state, output={}, critique=Critique(ok=True)
        )
    )
    assert second is not None
    assert second.metadata["replan_count"] == 2


def test_shopping_replanner_returns_none_for_good_result():
    plan = asyncio.run(
        ShoppingReplanner().replan(
            state=_state(poor=False),
            output={},
            critique=Critique(ok=True),
        )
    )
    assert plan is None


def test_shopping_replanner_respects_persisted_replan_count():
    state = _state(poor=True)
    replanner = ShoppingReplanner(max_replans=2)

    first = asyncio.run(
        replanner.replan(state=state, output={}, critique=Critique(ok=True))
    )
    assert first is not None
    state["plan"] = first.model_dump()
    assert first.metadata["search_queries"] == {}

    second = asyncio.run(
        replanner.replan(state=state, output={}, critique=Critique(ok=True))
    )
    assert second is not None
    state["plan"] = second.model_dump()
    assert second.metadata["search_queries"] == {}
    assert second.metadata["replan_count"] == 2

    third = asyncio.run(
        replanner.replan(state=state, output={}, critique=Critique(ok=True))
    )
    assert third is None


class PoorTool:
    async def ainvoke(self, _messages):
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "tool_a",
                    "args": {},
                    "id": uuid.uuid4().hex,
                    "type": "tool_call",
                }
            ],
        )


def test_runtime_bounds_replans_to_two():
    replan_calls = 0
    plan = Plan(goal="g", steps=[PlanStep(id="s", description="s")])

    class FakeReplanner:
        async def replan(self, *, state, output, critique):
            nonlocal replan_calls
            replan_calls += 1
            return plan

    async def handler(_args, _context):
        return ToolResult(
            call_id="",
            tool="tool_a",
            ok=True,
            data={"quality": "poor"},
        )

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="tool_a",
            description="tool_a",
            args_model=EmptyArgs,
            handler=handler,
        )
    )

    class Builder:
        def build(self, **_kwargs):
            return [SystemMessage(content="ctx")]

    class Policy:
        async def next_action(self, state, context):
            return await PoorTool().ainvoke(context)

    runtime = AgentRuntime(
        tools=registry,
        context_builder=Builder(),
        max_steps=3,
        replanner=FakeReplanner(),
        max_replans=2,
    )
    result = asyncio.run(runtime.run_turn(state=_state(poor=True), policy=Policy()))

    assert result.status == "max_steps"
    assert replan_calls == 2


def test_generic_runtime_and_policy_have_no_business_condition_names():
    root = Path(__file__).resolve().parents[1] / "app" / "agent"
    for name in ("runtime.py", "policies/plan_execute.py"):
        source = (root / name).read_text(encoding="utf-8").lower()
        for symbol in ("search_queries", "poor_quality", "推荐", "笔记本"):
            assert symbol not in source
