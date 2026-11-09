from fastapi.testclient import TestClient

from app.main import app
from app.store import store
from app.tools import ToolBox

client = TestClient(app)


def seed_session(**overrides):
    session = store.create("测试需求", overrides.pop("sites", ["jd"]))
    requirements = {
        "budget_cny": None,
        "must_have": [],
        "preferences": [],
        "category": None,
        "use_case": None,
    }
    requirements.update(overrides)
    session.requirements = requirements
    return session


def test_clarify_use_case_answer_is_merged():
    session = seed_session()
    session.pending_questions = [
        {
            "id": "usage",
            "field": "use_case",
            "question": "用途？",
            "options": ["办公", "开发"],
        }
    ]

    ToolBox(session).apply_clarify({"usage": "办公"})

    assert session.requirements["use_case"] == "办公"
    assert session.pending_questions == []


def test_clarify_must_have_answer_preserves_other_must_haves():
    session = seed_session(must_have=["防水", "32G内存"])
    session.pending_questions = [
        {
            "id": "ram",
            "field": "must_have",
            "question": "内存？",
            "options": ["16G", "32G"],
        }
    ]

    ToolBox(session).apply_clarify({"ram": "16G"})

    assert "防水" in session.requirements["must_have"]
    assert "16G内存" in session.requirements["must_have"]
    assert "32G内存" not in session.requirements["must_have"]


def test_clarify_preference_answer_is_merged():
    session = seed_session()
    session.pending_questions = [
        {
            "id": "brand",
            "field": "preferences",
            "question": "品牌偏好？",
            "options": ["Apple", "No preference"],
        }
    ]

    ToolBox(session).apply_clarify({"brand": "Apple"})

    assert "Apple" in session.requirements["preferences"]


def test_unlimited_budget_is_not_treated_as_missing():
    session = seed_session()
    session.pending_questions = [
        {
            "id": "budget",
            "field": "budget",
            "question": "预算？",
            "options": ["不限预算", "5000"],
        }
    ]

    ToolBox(session).apply_clarify({"budget": "不限预算"})

    assert session.requirements["budget_unlimited"] is True
    assert session.requirements["budget_cny"] is None


def test_clarify_unlimited_answer_clears_both_budget_bounds():
    session = seed_session(budget_cny=5000, budget_min_cny=3000)
    session.pending_questions = [
        {
            "id": "budget",
            "field": "budget",
            "question": "预算？",
            "options": ["不限预算", "5000"],
        }
    ]

    ToolBox(session).apply_clarify({"budget": "取消预算"})

    assert session.requirements["budget_unlimited"] is True
    assert session.requirements["budget_cny"] is None
    assert session.requirements["budget_min_cny"] is None


def test_clarify_bare_number_is_accepted_as_budget():
    session = seed_session()
    session.pending_questions = [
        {"id": "budget", "field": "budget", "question": "预算？", "options": []}
    ]

    ToolBox(session).apply_clarify({"budget": "5000"})

    assert session.requirements["budget_cny"] == 5000
    assert session.requirements["budget_unlimited"] is False


def test_clarify_budget_range_within_is_accepted():
    session = seed_session()
    session.pending_questions = [
        {"id": "budget", "field": "budget", "question": "预算？", "options": []}
    ]

    ToolBox(session).apply_clarify({"budget": "8000以内"})

    assert session.requirements["budget_cny"] == 8000


def test_clarify_unparseable_budget_requests_another_answer():
    session = seed_session()
    session.pending_questions = [
        {"id": "budget", "field": "budget", "question": "预算？", "options": []}
    ]

    result = ToolBox(session).apply_clarify({"budget": "看情况"})

    assert result["needs_clarify"] is True
    assert session.requirements["budget_cny"] is None


def test_unparseable_clarify_answer_is_reasked_via_model_tool(fake_llm):
    """Failed answer merge stays host-side; re-asking is a model clarify call."""
    import asyncio

    from conftest import _call
    from app.agent_runner import run_agent

    model = fake_llm(
        [
            _call(
                "clarify",
                questions=[
                    {
                        "id": "budget",
                        "field": "budget",
                        "question": "预算大概是多少？请给出一个上限，或选择「不限预算」。",
                        "options": [
                            "3000以内",
                            "3000-5000",
                            "5000-8000",
                            "8000-15000",
                            "1.5万以上",
                            "不限预算",
                        ],
                    }
                ],
            )
        ]
    )
    session = seed_session()
    session.pending_questions = [
        {"id": "budget", "field": "budget", "question": "预算？", "options": []}
    ]

    result = asyncio.run(run_agent(session, "", {"budget": "看情况"}))

    assert result["interrupted"] is True
    assert result["interrupt_reason"] == "clarify"
    assert result["clarify"][0]["field"] == "budget"
    assert model.calls == 1
    assert session.requirements["budget_cny"] is None


def test_resume_does_not_override_confirmed_budget(mock_shopper):
    session = seed_session()
    session.requirement = "预算5000的笔记本"
    session.requirements.update(
        {"budget_cny": 8000, "category": "笔记本"}
    )
    session.status = "interrupted"
    session.turn = 1

    body = client.post(
        f"/api/v1/sessions/{session.id}/resume", json={"note": "continue"}
    ).json()

    assert body["session_id"] == session.id
    detail = client.get(f"/api/v1/sessions/{session.id}").json()
    assert detail["requirements"]["budget_cny"] == 8000
