from __future__ import annotations

import asyncio

from app.agent.critic import Critique
from app.agent.state import AgentState, Plan, PlanStep
from app.config import settings
from app.domains.shopping.critic import ShoppingCritic
from app.store import store
from app.tools import ToolBox
from conftest import judged


def seed_session(**overrides):
    session = store.create("开发笔记本", ["jd"])
    session.requirements = {
        "budget_cny": 5000,
        "must_have": [],
        "category": "笔记本",
    }
    session.requirements.update(overrides)
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
    judged(ToolBox(session))
    return session


def output_for(*product_ids: str) -> dict:
    return {
        "reply": "推荐",
        "recommendations": [
            {
                "product_id": product_id,
                "title": "ThinkPad T14",
                "reason": "4800 元 32G",
            }
            for product_id in product_ids
        ],
    }


def state(plan: dict | None = None) -> AgentState:
    return {
        "run_id": "run-1",
        "messages": [],
        "plan": plan,
        "step": 3,
        "phase": "executing",
        "tool_results": [],
        "pending_user": None,
        "error": None,
    }


def test_over_budget_candidate_is_rejected():
    session = seed_session()
    session.products["p1"]["price_cny"] = 9000
    critic = ShoppingCritic(session, ToolBox(session))

    critique = asyncio.run(critic.evaluate(state=state(), output=output_for("p1")))

    assert critique.ok is False
    assert "budget" in critique.violations
    assert critique.retry is True
    assert critique.hints


def test_missing_detail_is_rejected(monkeypatch):
    monkeypatch.setattr(settings, "mock_browser", False)
    session = seed_session()
    session.products["p1"]["detail_fetched"] = False
    critic = ShoppingCritic(session, ToolBox(session))

    critique = asyncio.run(critic.evaluate(state=state(), output=output_for("p1")))

    assert critique.ok is False
    assert "detail" in critique.violations


def test_valid_candidate_passes_with_scores():
    session = seed_session()
    critic = ShoppingCritic(session, ToolBox(session))

    critique = asyncio.run(critic.evaluate(state=state(), output=output_for("p1")))

    assert critique.ok is True
    assert critique.retry is False
    assert critique.violations == []
    assert critique.scores["hard_constraints"] == 1.0
    assert critique.scores["evidence"] == 1.0


def test_empty_reply_is_rejected():
    session = seed_session()
    critic = ShoppingCritic(session, ToolBox(session))
    output = output_for("p1")
    output["reply"] = ""

    critique = asyncio.run(critic.evaluate(state=state(), output=output))

    assert critique.ok is False
    assert "empty_reply" in critique.violations
    assert critique.retry is True


def test_weak_recommendation_reason_is_rejected():
    session = seed_session()
    critic = ShoppingCritic(session, ToolBox(session))
    output = output_for("p1")
    output["recommendations"][0]["reason"] = ""

    critique = asyncio.run(critic.evaluate(state=state(), output=output))

    assert critique.ok is False
    assert any(violation.startswith("weak_evidence") for violation in critique.violations)


def test_duplicate_recommendations_are_rejected():
    session = seed_session()
    critic = ShoppingCritic(session, ToolBox(session))

    critique = asyncio.run(
        critic.evaluate(state=state(), output=output_for("p1", "p1"))
    )

    assert critique.ok is False
    assert any("duplicate" in violation for violation in critique.violations)


def test_plan_recommend_step_must_be_reached():
    session = seed_session()
    plan = Plan(
        goal="开发笔记本",
        steps=[
            PlanStep(id="search", description="search", status="done"),
            PlanStep(id="verify", description="verify", status="done"),
            PlanStep(id="recommend", description="recommend", status="pending"),
        ],
        metadata={
            "step_roles": {
                "search": "search",
                "verify": "verify",
                "recommend": "recommend",
            }
        },
    )
    critic = ShoppingCritic(session, ToolBox(session))

    critique = asyncio.run(
        critic.evaluate(
            state=state(plan.model_dump()), output=output_for("p1")
        )
    )

    assert critique.ok is False
    assert "plan_step_incomplete" in critique.violations


def test_generic_critique_has_no_business_fields():
    assert "product" not in Critique.model_fields
    assert "requirements" not in Critique.model_fields
    assert "inventory" not in Critique.model_fields
