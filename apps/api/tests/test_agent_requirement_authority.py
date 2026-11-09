import asyncio

from app.agent_runner import run_agent
from app.store import store
from app.tools import ToolBox


def seed_session(**overrides):
    session = store.create(overrides.pop("requirement", "测试需求"), ["jd"])
    requirements = {
        "budget_cny": None,
        "budget_min_cny": None,
        "budget_unlimited": False,
        "must_have": [],
        "preferences": [],
        "category": None,
        "use_case": None,
    }
    requirements.update(overrides)
    session.requirements = requirements
    return session


def test_unrelated_any_preference_does_not_clear_confirmed_budget():
    session = seed_session(budget_cny=8000)

    for text in ("品牌无所谓，预算8000", "预算8000，颜色都可以"):
        ToolBox(session).update_requirements(text)
        assert session.requirements["budget_cny"] == 8000
        assert session.requirements["budget_unlimited"] is False


def test_question_mentioning_a_price_does_not_overwrite_confirmed_budget():
    session = seed_session(budget_cny=8000)

    ToolBox(session).update_requirements("这个5000元的怎么样？")

    assert session.requirements["budget_cny"] == 8000
    assert session.requirements["budget_unlimited"] is False


def test_explicit_budget_change_overwrites_confirmed_budget():
    session = seed_session(budget_cny=8000)

    ToolBox(session).update_requirements("预算改成7000")

    assert session.requirements["budget_cny"] == 7000
    assert session.requirements["budget_unlimited"] is False


def test_question_mentioning_memory_does_not_overwrite_confirmed_ram():
    session = seed_session(must_have=["32G内存"])

    ToolBox(session).update_requirements("这个16G内存的够用吗？")

    assert "32G内存" in session.requirements["must_have"]
    assert "16G内存" not in session.requirements["must_have"]


def test_explicit_ram_change_overwrites_confirmed_ram():
    session = seed_session(must_have=["防水", "32G内存"])

    ToolBox(session).update_requirements("内存改成16G")

    assert "防水" in session.requirements["must_have"]
    assert "16G内存" in session.requirements["must_have"]
    assert "32G内存" not in session.requirements["must_have"]


def test_answers_without_pending_questions_do_not_reparse_original_requirement(
    monkeypatch,
):
    from app import llm

    monkeypatch.setattr(llm, "llm_enabled", lambda: False)
    session = seed_session(requirement="预算5000的笔记本", budget_cny=8000)

    asyncio.run(run_agent(session, "", {"budget": "5000"}))

    assert session.requirements["budget_cny"] == 8000
