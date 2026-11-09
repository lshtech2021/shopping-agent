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
    assert plan.metadata["search_queries"]["jd"] != ["开发笔记本"]
    assert "book" in plan.metadata["search_queries"]["jd"][0]
    assert plan.steps[-1].id.startswith("replan-")
    assert plan.steps[-1].status == "pending"


def test_shopping_replanner_returns_none_for_good_result():
    plan = asyncio.run(
        ShoppingReplanner().replan(
            state=_state(poor=False),
            output={},
            critique=Critique(ok=True),
        )
    )
    assert plan is None


def test_shopping_replanner_respects_max_two_replans():
    state = _state(poor=True)
    replanner = ShoppingReplanner(max_replans=2)

    first = asyncio.run(
        replanner.replan(state=state, output={}, critique=Critique(ok=True))
    )
    second = asyncio.run(
        replanner.replan(state=state, output={}, critique=Critique(ok=True))
    )
    third = asyncio.run(
        replanner.replan(state=state, output={}, critique=Critique(ok=True))
    )

    assert first is not None
    assert second is not None
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
