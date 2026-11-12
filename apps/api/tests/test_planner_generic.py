"""The planner must not synthesize category-specific queries in code."""
from __future__ import annotations

import asyncio

from app.agent.state import Plan, PlanStep
from app.domains.shopping.planner import ShoppingPlanner, planning_ready


class StaticPlanModel:
    def __init__(self, plan: Plan):
        self.plan = plan
        self.calls = 0

    def with_structured_output(self, _schema):
        return self

    async def ainvoke(self, _messages):
        self.calls += 1
        return self.plan


class FailingModel:
    def with_structured_output(self, _schema):
        return self

    async def ainvoke(self, _messages):
        raise RuntimeError("planner unavailable")


def test_planning_ready_is_a_structural_check_only():
    assert planning_ready(
        {"category": "widget", "budget": 1000},
        sites=["ebay"],
    )
    assert planning_ready(
        {"category": "widget", "budget_unlimited": True},
        sites=["ebay"],
    )
    assert not planning_ready(
        {"category": "widget", "budget": None},
        sites=["ebay"],
    )
    assert not planning_ready(
        {"category": None, "budget": 1000},
        sites=["ebay"],
    )
    assert not planning_ready(
        {"category": "widget", "budget": 1000},
        sites=[],
    )


def test_fallback_plan_needs_no_model_call():
    result = ShoppingPlanner(model=FailingModel()).fallback(
        goal="budget 1000 widget",
        context={"requirements": {}, "sites": ["ebay"]},
    )

    assert result.metadata["search_queries"] == {}
    assert result.metadata["planner"] == "fallback"
    assert [step.id for step in result.steps] == [
        "search",
        "verify",
        "recommend",
    ]


def test_planner_uses_model_even_when_requirements_are_complete():
    plan = Plan(
        goal="find a widget",
        steps=[PlanStep(id="search", description="search")],
        metadata={"search_queries": {"jd": ["user-derived query"]}},
    )
    model = StaticPlanModel(plan)

    result = asyncio.run(
        ShoppingPlanner(model=model).plan(
            goal="预算1000的widget",
            context={
                "requirements": {
                    "category": "widget",
                    "budget": 1000,
                    "budget_unlimited": False,
                },
                "sites": ["jd"],
            },
        )
    )

    assert model.calls == 1
    assert result.metadata["search_queries"] == {"jd": ["user-derived query"]}


def test_planner_fallback_does_not_generate_a_hardcoded_category_query():
    result = asyncio.run(
        ShoppingPlanner(model=FailingModel()).plan(
            goal="预算1000的widget",
            context={
                "requirements": {
                    "category": "widget",
                    "use_case": "some use case",
                    "budget": 1000,
                },
                "sites": ["ebay"],
            },
        )
    )

    assert result.metadata["search_queries"] == {}
    assert result.metadata["planner"] == "fallback"
    assert [step.id for step in result.steps] == [
        "search",
        "verify",
        "recommend",
    ]
