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


def test_record_plan_evidence_does_not_complete_out_of_order_step():
    state = _plan_state()

    record_plan_evidence(
        state,
        ToolResult(
            call_id="c1",
            tool="recommend",
            ok=True,
            meta={
                "plan_step_id": "recommend",
                "evidence": ["premature"],
                "plan_step_done": True,
            },
        ),
    )

    plan = Plan.model_validate(state["plan"])
    assert plan.steps[0].status == "pending"
    assert plan.steps[1].status == "pending"
    assert plan.steps[2].status == "in_progress"
    assert plan.steps[2].evidence == ["premature"]


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


def test_real_shopping_tools_progress_through_all_plan_steps(monkeypatch):
    session = store.create("开发笔记本", ["jd"])
    session.requirements.update({"category": "笔记本", "budget_cny": 5000})
    session.products["p1"] = {
        "product_id": "p1",
        "source": "jd",
        "source_product_id": "1001",
        "title": "ThinkPad T14 32G 开发本",
        "price": 4800.0,
        "currency": "CNY",
        "price_cny": 4800.0,
        "url": "https://item.jd.com/1001.html",
        "detail_fetched": True,
        "attributes": {"内存": "32GB"},
    }
    plan = Plan(
        goal="开发笔记本",
        steps=[
            PlanStep(id="search", description="search"),
            PlanStep(id="verify", description="verify"),
            PlanStep(id="recommend", description="recommend"),
        ],
        metadata={
            "step_roles": {
                "search": "search",
                "verify": "verify",
                "recommend": "recommend",
            }
        },
    )
    state = _plan_state()
    state["plan"] = plan.model_dump()
    box = ToolBox(session)
    specs = {spec.name: spec for spec in build_shopping_tools(box, state)}

    async def fake_search(*_args, **_kwargs):
        return {
            "status": "ok",
            "site": "jd",
            "query": "开发笔记本",
            "items": [{"source_product_id": "1001"}],
            "result_count": 1,
        }

    async def fake_scrape(*_args, **_kwargs):
        return {
            "status": "ok",
            "items": [{"source_product_id": "1001", "title": "ThinkPad"}],
            "page": 1,
            "accepted": 1,
            "product_count": 1,
        }

    async def fake_detail(*_args, **_kwargs):
        return {
            "status": "ok",
            "product_id": "p1",
            "product": {"product_id": "p1"},
        }

    monkeypatch.setattr(box, "search", fake_search)
    monkeypatch.setattr(box, "scrape_listing", fake_scrape)
    monkeypatch.setattr(box, "fetch_detail", fake_detail)
    context = ToolContext(
        run_id=session.id,
        memory=object(),
        browser=object(),
        trace=object(),
        resources={},
    )

    async def run(name, args):
        spec = specs[name]
        parsed = spec.args_model.model_validate(args)
        result = await spec.handler(parsed, context)
        record_plan_evidence(state, result)
        return result

    search_result = asyncio.run(
        run("search", {"site": "jd", "query": "开发笔记本"})
    )
    assert search_result.meta["plan_step_id"] == "search"
    assert search_result.meta.get("plan_step_done") is not True

    scrape_result = asyncio.run(run("scrape_listing", {"site": "jd"}))
    assert scrape_result.meta["plan_step_id"] == "search"
    assert scrape_result.meta["plan_step_done"] is True

    detail_result = asyncio.run(
        run("fetch_detail", {"product_id": "p1"})
    )
    assert detail_result.meta["plan_step_id"] == "verify"
    assert detail_result.meta["plan_step_done"] is True

    recommend_result = asyncio.run(
        run(
            "recommend",
            {
                "picks": [{"product_id": "p1", "reason": "4800 元 32G"}],
                "answer": "推荐 ThinkPad",
            },
        )
    )
    assert recommend_result.meta["plan_step_id"] == "recommend"

    final = Plan.model_validate(state["plan"])
    assert [step.status for step in final.steps] == [
        "done",
        "done",
        "done",
    ]
    assert all(step.evidence for step in final.steps)


def test_parallel_tool_calls_share_one_plan_step_snapshot():
    seen: list[str | None] = []

    async def first(_args, context):
        seen.append(context.resources.get("plan_step_id"))
        return ToolResult(
            call_id="",
            tool="first",
            ok=True,
            meta={"plan_step_id": "s1", "plan_step_done": True},
        )

    async def second(_args, context):
        seen.append(context.resources.get("plan_step_id"))
        return ToolResult(call_id="", tool="second", ok=True)

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="first",
            description="first",
            args_model=EmptyArgs,
            handler=first,
        )
    )
    registry.register(
        ToolSpec(
            name="second",
            description="second",
            args_model=EmptyArgs,
            handler=second,
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
                            "name": "first",
                            "args": {},
                            "id": uuid.uuid4().hex,
                            "type": "tool_call",
                        },
                        {
                            "name": "second",
                            "args": {},
                            "id": uuid.uuid4().hex,
                            "type": "tool_call",
                        },
                    ],
                )
            return AIMessage(content="done")

    class Builder:
        def build(self, **_kwargs):
            return [SystemMessage(content="ctx")]

    runtime = AgentRuntime(
        tools=registry,
        context_builder=Builder(),
        max_steps=3,
    )
    from app.agent.policies.react import ReActPolicy

    asyncio.run(
        runtime.run_turn(
            state=_runtime_state(),
            policy=ReActPolicy(model=Model()),
        )
    )

    assert seen == ["search", "search"]


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
