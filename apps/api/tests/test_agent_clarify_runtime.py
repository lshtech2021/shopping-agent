from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

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
    assert session.requirements["budget_cny"] is None


def test_card_budget_reply_is_applied_before_continuing(fake_llm):
    seen = []

    def reply(messages):
        seen.extend(
            message.content
            for message in messages
            if isinstance(message, HumanMessage)
        )
        return AIMessage(content="收到")

    fake_llm([reply])
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
    assert detail["requirements"]["budget_cny"] == 5000
    assert any(
        message["role"] == "user" and message["content"] == content
        for message in detail["messages"]
    )
    assert any(content in message for message in seen)


def test_deprecated_answers_are_formatted_as_a_user_message(fake_llm):
    fake_llm([AIMessage(content="收到")])
    session = seed_session()
    session.pending_questions = [
        {"id": "budget", "field": "budget", "question": "预算？", "options": []}
    ]

    response = client.post(
        f"/api/v1/sessions/{session.id}/messages",
        json={"answers": {"budget": "5000", "category": "笔记本", "empty": " "}},
    )

    assert response.status_code == 200
    detail = client.get(f"/api/v1/sessions/{session.id}").json()
    assert any(
        message["role"] == "user"
        and message["content"] == "budget: 5000\ncategory: 笔记本"
        for message in detail["messages"]
    )


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
