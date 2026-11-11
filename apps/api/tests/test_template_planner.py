"""Complete requirements should use a template plan without an LLM call."""
from __future__ import annotations

import asyncio

from app.domains.shopping.planner import ShoppingPlanner, requirements_complete


class CountingModel:
    def __init__(self):
        self.calls = 0

    def with_structured_output(self, _schema):
        return self

    async def ainvoke(self, _messages):
        self.calls += 1
        raise AssertionError("template planner must not call the model")


def test_requirements_complete_detects_budget_and_category():
    assert requirements_complete(
        {"category": "笔记本", "budget_cny": 5000}, sites=["jd"]
    )
    assert requirements_complete(
        {"category": "笔记本", "budget_unlimited": True}, sites=["jd"]
    )
    assert not requirements_complete(
        {"category": "笔记本", "budget_cny": None}, sites=["jd"]
    )
    assert not requirements_complete(
        {"category": None, "budget_cny": 5000}, sites=["jd"]
    )
    assert not requirements_complete(
        {"category": "笔记本", "budget_cny": 5000}, sites=[]
    )


def test_planner_uses_template_when_requirements_complete():
    model = CountingModel()
    planner = ShoppingPlanner(model=model)
    plan = asyncio.run(
        planner.plan(
            goal="预算5000的笔记本",
            context={
                "requirements": {
                    "category": "笔记本",
                    "budget_cny": 5000,
                    "budget_unlimited": False,
                },
                "sites": ["jd", "ebay"],
            },
        )
    )
    assert model.calls == 0
    assert plan.goal
    assert [step.id for step in plan.steps] == ["search", "verify", "recommend"]
    assert plan.metadata["search_queries"]["jd"]
    assert plan.metadata["search_queries"]["ebay"] == ["laptop"]


def test_template_plan_uses_short_ebay_query_without_use_case():
    planner = ShoppingPlanner(model=CountingModel())
    plan = asyncio.run(
        planner.plan(
            goal="预算5000的笔记本电脑，用于软件开发",
            context={
                "requirements": {
                    "category": "笔记本电脑",
                    "use_case": "软件开发",
                    "budget_cny": 5000,
                    "budget_unlimited": False,
                },
                "sites": ["ebay"],
            },
        )
    )

    assert plan.metadata["search_queries"]["ebay"] == ["laptop"]
