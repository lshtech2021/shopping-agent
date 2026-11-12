from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from app.main import app
from conftest import _call
from app.store import store
from app.tools import ToolBox

client = TestClient(app)


def seed_session(**overrides):
    session = store.create("测试需求", overrides.pop("site", "jd"))
    requirements = {
        "budget": None,
        "must_have": [],
        "preferences": [],
        "category": None,
        "use_case": None,
    }
    requirements.update(overrides)
    session.requirements = requirements
    return session


def test_toolbox_has_no_apply_clarify_merger():
    assert not hasattr(ToolBox, "apply_clarify")


def test_unparseable_answer_is_reasked_via_model_tool(fake_llm):
    """The answer remains plain content, so the model decides to ask again."""
    import asyncio

    from conftest import _call
    from app.agent_runner import run_agent

    model = fake_llm(
        [
            _call(
                "ask_user",
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
                reason="clarify",
            )
        ]
    )
    session = seed_session()
    session.pending_questions = [
        {"id": "budget", "field": "budget", "question": "预算？", "options": []}
    ]

    result = asyncio.run(run_agent(session, "budget: 看情况"))

    assert result["interrupted"] is True
    assert result["interrupt_reason"] == "clarify"
    assert result["clarify"][0]["field"] == "budget"
    assert model.calls == 1
    assert session.requirements["budget"] is None


def test_update_requirements_clarify_renders_card_and_persists_pending(
    fake_llm, monkeypatch
):
    import asyncio

    from app.agent.state import Plan, PlanStep
    from app.agent_runner import run_agent
    from app.domains.shopping import tools as shopping_tools
    from app.requirement_review import RequirementReview

    async def fake_review(_requirements, _session=None):
        return RequirementReview(
            hard_must_have=["waterproof"],
            clarify_question="What threshold?",
            clarify_options=["A", "B"],
        )

    monkeypatch.setattr(shopping_tools, "_review_requirements", fake_review)
    fake_llm(
        [_call("update_requirements", must_have=["waterproof"])]
    )
    session = seed_session()
    session.plan = Plan(
        goal="g",
        steps=[PlanStep(id="search", description="search")],
        metadata={
            "step_roles": {
                "search": "search",
                "verify": "verify",
                "recommend": "recommend",
            }
        },
    ).model_dump()

    result = asyncio.run(run_agent(session, "我需要防水"))

    assert result["interrupt_reason"] == "clarify"
    assert result["clarify"] == [
        {
            "id": "must_have",
            "field": "must_have",
            "question": "What threshold?",
            "options": ["A", "B"],
        }
    ]
    assert session.pending_questions == result["clarify"]


def test_card_reply_is_a_chat_message_the_model_saves(fake_llm):
    seen = []

    def save_answers(messages):
        seen.extend(
            message.content
            for message in messages
            if isinstance(message, HumanMessage)
        )
        return _call("update_requirements", budget=5000, category="笔记本")

    fake_llm([save_answers, AIMessage(content="收到")])
    session = seed_session()
    session.status = "interrupted"
    session.interrupt_reason = "clarify"
    session.pending_questions = [
        {"id": "budget", "field": "budget", "question": "预算？", "options": []}
    ]

    content = "budget: 5000\ncategory: 笔记本"

    response = client.post(
        f"/api/v1/sessions/{session.id}/messages",
        json={"content": content},
    )

    assert response.status_code == 200
    detail = client.get(f"/api/v1/sessions/{session.id}").json()
    assert detail["pending_questions"] == []
    assert detail["requirements"]["budget"] == 5000
    assert any(
        message["role"] == "user" and message["content"] == content
        for message in detail["messages"]
    )
    assert any(content in message for message in seen)


def test_resume_does_not_override_confirmed_budget(mock_shopper):
    session = seed_session()
    session.requirement = "预算5000的笔记本"
    session.requirements.update(
        {"budget": 8000, "category": "笔记本"}
    )
    session.status = "interrupted"
    session.turn = 1

    body = client.post(
        f"/api/v1/sessions/{session.id}/resume", json={"note": "continue"}
    ).json()

    assert body["session_id"] == session.id
    detail = client.get(f"/api/v1/sessions/{session.id}").json()
    assert detail["requirements"]["budget"] == 8000
