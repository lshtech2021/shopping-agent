from __future__ import annotations

import asyncio
from pathlib import Path

from app.agent.runtime import TurnResult
from app.agent.state import PendingUser, Plan, PlanStep
from app.domains.shopping.adapter import initial_state, to_message_response
from app.domains.shopping.state import ShoppingOutput, ShoppingState
from app.domains.shopping.tools import build_shopping_tools
from app.store import store
from app.tools import ToolBox


class Context:
    pass


def seed_session():
    session = store.create("需要一台开发笔记本", "jd")
    session.requirements = {
        "budget_cny": 5000,
        "must_have": [],
        "category": "笔记本",
    }
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
    return session


def test_shopping_state_and_output_are_domain_models():
    state = ShoppingState(requirements={"budget_cny": 5000}, sites=["jd"])
    output = ShoppingOutput(reply="推荐", recommendations=[{"product_id": "p1"}])
    assert state.sites == ["jd"]
    assert output.recommendations[0]["product_id"] == "p1"


def test_build_shopping_tools_exposes_one_ask_user_wait_tool():
    session = seed_session()
    specs = build_shopping_tools(ToolBox(session), {})

    names = [spec.name for spec in specs]
    assert names == [
        "get_session",
        "update_requirements",
        "query_inventory",
        "search",
        "scrape_listing",
        "next_page",
        "fetch_detail",
        "recommend",
        "ask_user",
    ]
    assert "clarify" not in names
    assert all(spec.args_model is not None for spec in specs)


def test_state_mutating_shopping_tools_are_not_marked_read_only():
    session = seed_session()
    specs = {
        spec.name: spec
        for spec in build_shopping_tools(ToolBox(session), {})
    }

    for name in ("update_requirements", "recommend", "ask_user"):
        assert specs[name].read_only is False
    assert specs["get_session"].read_only is True
    assert specs["query_inventory"].read_only is True


def test_recommend_sets_finish_control_and_output():
    session = seed_session()
    spec = next(
        item
        for item in build_shopping_tools(ToolBox(session), {})
        if item.name == "recommend"
    )
    args = spec.args_model.model_validate(
        {
            "picks": [{"product_id": "p1", "reason": "4800 元 32G"}],
            "answer": "推荐 ThinkPad",
        }
    )

    result = asyncio.run(spec.handler(args, Context()))

    assert result.control == "finish"
    assert result.output["reply"] == "推荐 ThinkPad"
    assert result.output["recommendations"][0]["product_id"] == "p1"


def test_recommend_proposal_does_not_commit_until_finalize():
    session = seed_session()
    spec = next(
        item
        for item in build_shopping_tools(ToolBox(session), {})
        if item.name == "recommend"
    )
    args = spec.args_model.model_validate(
        {
            "picks": [{"product_id": "p1", "reason": "4800 元 32G"}],
            "answer": "推荐 ThinkPad",
        }
    )

    result = asyncio.run(spec.handler(args, Context()))

    assert result.control == "finish"
    assert session.recommendations == []
    assert session.status != "done"

    committed = ToolBox(session).recommend(
        result.output["picks"], result.output["reply"]
    )
    assert committed["ok"] is True
    assert session.recommendations[0]["product_id"] == "p1"
    assert session.status == "done"


def test_ask_user_with_empty_options_sets_wait_user_control():
    session = seed_session()
    specs = {
        item.name: item
        for item in build_shopping_tools(ToolBox(session), {})
    }

    ask_args = specs["ask_user"].args_model.model_validate(
        {
            "questions": [
                {
                    "id": "budget",
                    "field": "budget",
                    "question": "预算多少？",
                    "options": [],
                }
            ],
            "reason": "need_user",
        }
    )
    ask_result = asyncio.run(specs["ask_user"].handler(ask_args, Context()))
    assert ask_result.control == "wait_user"
    assert ask_result.output["pending_user"]["question"] == "预算多少？"
    questions = ask_result.output["pending_user"]["metadata"]["questions"]
    assert questions[0]["id"] == "budget"
    assert session.pending_questions == questions


def test_to_message_response_maps_finish_and_waiting():
    done = TurnResult(
        run_id="run-1",
        status="done",
        output={
            "reply": "推荐",
            "recommendations": [
                {"product_id": "p1", "title": "ThinkPad", "reason": "32G"}
            ],
        },
        steps=2,
    )
    response = to_message_response(done)
    assert response.reply == "推荐"
    assert response.interrupted is False

    waiting = TurnResult(
        run_id="run-1",
        status="waiting_user",
        pending_user=PendingUser(
            question="请确认预算",
            reason="need_user",
            metadata={
                "questions": [
                    {"id": "budget", "field": "budget", "question": "预算？"},
                    {"id": "category", "field": "category", "question": "品类？"},
                ]
            },
        ),
        steps=1,
    )
    response = to_message_response(waiting)
    assert response.interrupted is True
    assert response.interrupt_reason == "need_user"
    assert response.clarify[0].id == "budget"
    assert response.clarify[1].id == "category"


def test_critic_rejected_result_is_resumable_and_has_no_recommendations():
    result = TurnResult(
        run_id="run-1",
        status="failed",
        error="critic_rejected",
        steps=4,
    )
    response = to_message_response(result)
    assert response.interrupted is True
    assert response.interrupt_reason == "critic_rejected"
    assert response.recommendations == []


def test_llm_agent_no_longer_contains_the_old_graph():
    path = Path(__file__).resolve().parents[1] / "app" / "llm_agent.py"
    source = path.read_text(encoding="utf-8")
    assert "StateGraph" not in source
    assert "StructuredTool" not in source
    assert "AgentRuntime" in source


def test_initial_state_rehydrates_a_persisted_plan():
    session = seed_session()
    plan = Plan(
        goal="pick a laptop",
        steps=[PlanStep(id="search", description="search")],
        metadata={"search_queries": {"jd": ["笔记本"]}},
    )
    session.plan = plan.model_dump()

    state = initial_state(session, "继续")

    assert state["plan"] == session.plan


def test_run_llm_policy_creates_and_persists_a_plan(monkeypatch):
    from fastapi.testclient import TestClient

    from app import llm
    from app.main import app
    from conftest import _call, _recommend_from_inventory

    client = TestClient(app)
    plan = Plan(
        goal="pick a laptop",
        steps=[
            PlanStep(id="analyze", description="analyze requirements"),
            PlanStep(id="search", description="search"),
            PlanStep(id="verify", description="verify"),
            PlanStep(id="recommend", description="recommend"),
        ],
        metadata={
            "search_queries": {"jd": ["笔记本"]},
            "evidence_checklist": ["price", "ram"],
            "stop_conditions": ["three candidates"],
            "step_roles": {
                "search": "search",
                "verify": "verify",
                "recommend": "recommend",
            },
        },
    )

    class PlanningModel:
        def __init__(self):
            self.calls = 0
            self.plan_calls = 0

        def with_structured_output(self, _schema):
            outer = self

            class StructuredPlan:
                async def ainvoke(self, _messages):
                    outer.plan_calls += 1
                    return plan

            return StructuredPlan()

        def bind_tools(self, _tools):
            return self

        async def ainvoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                return _call("search", site="jd", query="笔记本")
            if self.calls == 2:
                return _call("scrape_listing", site="jd")
            if self.calls == 3:
                return _call("query_inventory", limit=10)
            return _recommend_from_inventory(messages)

    model = PlanningModel()
    monkeypatch.setattr(llm, "llm_enabled", lambda: True)
    monkeypatch.setattr(llm, "build_chat_model", lambda: model)

    created = client.post(
        "/api/v1/sessions",
        json={"requirement": "笔记本 预算5000", "site": "jd"},
    ).json()
    # Requirements already confirmed (e.g. from an earlier turn).
    store.get(created["session_id"]).requirements.update(
        {"budget_cny": 5000, "category": "笔记本"}
    )
    response = client.post(
        f"/api/v1/sessions/{created['session_id']}/messages",
        json={"content": "笔记本 预算5000"},
    ).json()

    # Complete requirements use the template planner (no LLM plan call).
    assert model.plan_calls == 0
    assert response["recommendations"]
    assert response["status"] == "done"
    assert response["plan"]["metadata"].get("planner") == "template"
    assert [step["id"] for step in response["plan"]["steps"]] == [
        "search",
        "verify",
        "recommend",
    ]
    assert response["plan"]["steps"][-1]["status"] == "done"
    detail = client.get(f"/api/v1/sessions/{created['session_id']}").json()
    assert detail["plan"]["steps"][0]["id"] == "search"
    assert detail["recommendations"]
