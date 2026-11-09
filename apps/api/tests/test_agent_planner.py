from __future__ import annotations

import asyncio
import json

from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.policies.plan_execute import Planner
from app.agent.state import Plan, PlanStep
from app.domains.shopping.planner import ShoppingPlanner


class FakePlanModel:
    def __init__(self, result):
        self.result = result
        self.messages = []
        self.schema = None

    def with_structured_output(self, schema):
        self.schema = schema
        return self

    async def ainvoke(self, messages):
        self.messages = messages
        return self.result


def test_generic_planner_protocol_accepts_a_domain_planner():
    class FakePlanner:
        async def plan(self, *, goal, context):
            return Plan(goal=goal, steps=[])

    assert isinstance(FakePlanner(), Planner)


def test_shopping_planner_returns_steps_and_business_metadata():
    result = Plan(
        goal="pick a laptop",
        steps=[
            PlanStep(id="search", description="search both sites"),
            PlanStep(id="verify", description="verify details"),
            PlanStep(id="recommend", description="recommend one"),
        ],
        metadata={
            "search_queries": {"jd": ["开发笔记本"], "ebay": ["developer laptop"]},
            "evidence_checklist": ["price", "ram", "detail"],
            "stop_conditions": ["three verified candidates"],
        },
    )
    model = FakePlanModel(result)
    planner = ShoppingPlanner(model=model)

    plan = asyncio.run(
        planner.plan(
            goal="pick a laptop",
            context={"requirements": {"budget_cny": 5000}, "sites": ["jd"]},
        )
    )

    assert [step.id for step in plan.steps] == ["search", "verify", "recommend"]
    assert plan.metadata["search_queries"]["jd"] == ["开发笔记本"]
    assert model.schema is Plan
    assert any(isinstance(message, SystemMessage) for message in model.messages)
    assert any(isinstance(message, HumanMessage) for message in model.messages)
    assert "5000" in model.messages[-1].content


def test_generic_plan_has_no_business_fields():
    assert "search_queries" not in Plan.model_fields
    assert "evidence_checklist" not in Plan.model_fields
    assert "stop_conditions" not in Plan.model_fields


def test_shopping_planner_normalizes_missing_plan_metadata():
    result = Plan(goal="pick a phone", steps=[PlanStep(id="search", description="search")])
    planner = ShoppingPlanner(model=FakePlanModel(result))

    plan = asyncio.run(planner.plan(goal="pick a phone", context={}))

    assert plan.metadata["search_queries"] == {}
    assert plan.metadata["evidence_checklist"] == []
    assert plan.metadata["stop_conditions"] == []
    json.dumps(plan.model_dump())
