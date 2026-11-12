import asyncio

from langchain_core.messages import AIMessage, HumanMessage

from app.agent_runner import run_agent
from app.llm_agent import run_llm_policy
from app.store import store
from app.tools import ToolBox
from conftest import _call


def seed_session(**overrides):
    session = store.create(overrides.pop("requirement", "测试需求"), "jd")
    requirements = {
        "budget": None,
        "budget_min": None,
        "budget_unlimited": False,
        "must_have": [],
        "preferences": [],
        "category": None,
        "use_case": None,
    }
    requirements.update(overrides)
    session.requirements = requirements
    return session


def test_explicit_budget_change_overwrites_confirmed_budget():
    session = seed_session(budget=8000)

    ToolBox(session).update_requirements(budget=7000)

    assert session.requirements["budget"] == 7000
    assert session.requirements["budget_unlimited"] is False


def test_unrelated_fields_do_not_clear_confirmed_budget():
    session = seed_session(budget=8000)

    ToolBox(session).update_requirements(preferences=["颜色都可以"])

    assert session.requirements["budget"] == 8000
    assert session.requirements["budget_unlimited"] is False


def test_model_replaces_must_have_from_user_text_with_complete_list(fake_llm):
    session = seed_session(must_have=["water resistant", "model A"])
    text = "change the model to B"

    def update_from_user(messages):
        assert any(
            text in str(message.content)
            for message in messages
            if isinstance(message, HumanMessage)
        )
        # The tool contract is a complete list, so the LLM repeats the
        # unaffected item instead of relying on backend semantic merging.
        return _call(
            "update_requirements",
            must_have=["water resistant", "model B"],
        )

    fake_llm([update_from_user, AIMessage(content="updated")])
    asyncio.run(
        run_llm_policy(
            session,
            text,
            ToolBox(session),
        )
    )

    assert session.requirements["must_have"] == [
        "water resistant",
        "model B",
    ]


def test_answers_without_pending_questions_do_not_reparse_original_requirement(
    monkeypatch,
):
    from app import llm

    monkeypatch.setattr(llm, "llm_enabled", lambda: False)
    session = seed_session(requirement="预算5000的笔记本", budget=8000)

    asyncio.run(run_agent(session, ""))

    assert session.requirements["budget"] == 8000
