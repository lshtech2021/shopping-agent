from __future__ import annotations

import asyncio
import uuid

from langchain_core.messages import AIMessage, SystemMessage
from pydantic import BaseModel

from app.agent.policies.plan_execute import (
    PlanExecutePolicy,
    record_plan_evidence,
)
from app.agent.runtime import AgentRuntime
from app.agent.state import AgentState, Plan, PlanStep
from app.agent.tools import ToolContext, ToolRegistry, ToolResult, ToolSpec
from app.domains.shopping.tools import build_shopping_tools
from app.store import store
from app.tools import ToolBox


class EmptyArgs(BaseModel):
    pass


def _plan_state() -> dict:
    plan = Plan(
        goal="buy a laptop",
        steps=[
            PlanStep(id="search", description="search"),
            PlanStep(id="verify", description="verify"),
            PlanStep(id="recommend", description="recommend"),
        ],
    )
    return {
        "run_id": "run-1",
        "messages": [],
        "plan": plan.model_dump(),
        "step": 0,
        "phase": "executing",
        "tool_results": [],
        "pending_user": None,
        "error": None,
    }


def test_record_plan_evidence_updates_only_the_named_step():
    state = _plan_state()

    record_plan_evidence(
        state,
        ToolResult(
            call_id="c1",
            tool="search",
            ok=True,
            meta={"plan_step_id": "search", "evidence": ["query:jd laptop"]},
        ),
    )

    plan = Plan.model_validate(state["plan"])
    assert plan.steps[0].status == "in_progress"
    assert plan.steps[0].evidence == ["query:jd laptop"]
    assert plan.steps[1].status == "pending"
    assert plan.steps[2].status == "pending"
    assert all(step.status != "skipped" for step in plan.steps)


def test_record_plan_evidence_marks_done_when_requested():
    state = _plan_state()

    record_plan_evidence(
        state,
        ToolResult(
            call_id="c1",
            tool="search",
            ok=True,
            meta={
                "plan_step_id": "search",
                "evidence": ["10 items"],
                "plan_step_done": True,
            },
        ),
    )

    plan = Plan.model_validate(state["plan"])
    assert plan.steps[0].status == "done"
    assert plan.steps[0].evidence == ["10 items"]


def test_record_plan_evidence_ignores_unknown_step():
    state = _plan_state()

    record_plan_evidence(
        state,
        ToolResult(
            call_id="c1",
            tool="search",
            ok=True,
            meta={"plan_step_id": "missing", "evidence": ["x"]},
        ),
    )

    plan = Plan.model_validate(state["plan"])
    assert all(step.status == "pending" for step in plan.steps)


class Delegate:
    def __init__(self):
        self.called = False

    async def next_action(self, state, context):
        self.called = True
        return AIMessage(content="ok")


def test_plan_execute_policy_advances_first_pending_step():
    state: AgentState = _plan_state()
    delegate = Delegate()
    policy = PlanExecutePolicy(delegate=delegate)

    asyncio.run(policy.next_action(state, [SystemMessage(content="ctx")]))

    plan = Plan.model_validate(state["plan"])
    assert delegate.called is True
    assert plan.steps[0].status == "in_progress"
    assert plan.steps[1].status == "pending"


def _runtime_state() -> AgentState:
    return _plan_state()


def test_runtime_updates_plan_from_tool_meta():
    seen_step = {}

    async def handler(_args, context):
        seen_step["id"] = context.resources.get("plan_step_id")
        return ToolResult(
            call_id="",
            tool="tool_a",
            ok=True,
            meta={
                "plan_step_id": "search",
                "evidence": ["found 3 candidates"],
                "plan_step_done": True,
            },
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

    class Model:
        def __init__(self):
            self.calls = 0

        async def ainvoke(self, _messages):
            self.calls += 1
            if self.calls == 1:
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
            return AIMessage(content="done")

    class Builder:
        def build(self, **_kwargs):
            return [SystemMessage(content="ctx")]

    runtime = AgentRuntime(
        tools=registry,
        context_builder=Builder(),
        max_steps=4,
    )
    from app.agent.policies.react import ReActPolicy

    result = asyncio.run(
        runtime.run_turn(
            state=_runtime_state(),
            policy=ReActPolicy(model=Model()),
        )
    )

    assert result.status == "done"
    assert seen_step["id"] == "search"


def test_shopping_search_result_carries_plan_step_and_evidence(monkeypatch):
    session = store.create("laptop", ["jd"])
    box = ToolBox(session)
    specs = {spec.name: spec for spec in build_shopping_tools(box, {})}

    async def fake_search(*_args, **_kwargs):
        return {
            "status": "ok",
            "site": "jd",
            "query": "laptop",
            "items": [{"source_product_id": "1"}, {"source_product_id": "2"}],
        }

    monkeypatch.setattr(box, "search", fake_search)
    context = ToolContext(
        run_id=session.id,
        memory=object(),
        browser=object(),
        trace=object(),
        resources={"plan_step_id": "search"},
    )
    args = specs["search"].args_model.model_validate(
        {"site": "jd", "query": "laptop"}
    )

    result = asyncio.run(specs["search"].handler(args, context))

    assert result.meta["plan_step_id"] == "search"
    assert any("2" in evidence for evidence in result.meta["evidence"])
