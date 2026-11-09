"""Golden-turn cost budgets for a complete shopping requirement."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from conftest import MockShopper

client = TestClient(app)

# Complete requirements should not need clarify or get_session thrash.
MAX_LLM_STEPS = 10
MAX_GET_SESSION_CALLS = 0


def test_complete_requirement_turn_stays_within_cost_budget(monkeypatch, mock_shopper):
    model: MockShopper = mock_shopper
    original = model.ainvoke

    get_session_calls = {"n": 0}

    async def counting_ainvoke(messages):
        reply = await original(messages)
        tool_calls = getattr(reply, "tool_calls", None) or []
        for call in tool_calls:
            if call.get("name") == "get_session":
                get_session_calls["n"] += 1
        return reply

    monkeypatch.setattr(model, "ainvoke", counting_ainvoke)

    created = client.post(
        "/api/v1/sessions",
        json={
            "requirement": "帮我挑一台 5000 元内用于开发的笔记本，内存至少 32G",
            "sites": ["jd"],
        },
    ).json()
    body = client.post(
        f"/api/v1/sessions/{created['session_id']}/messages",
        json={
            "content": "帮我挑一台 5000 元内用于开发的笔记本，内存至少 32G"
        },
    ).json()

    assert body["interrupted"] is False
    assert body["recommendations"]
    assert model.calls <= MAX_LLM_STEPS
    assert get_session_calls["n"] <= MAX_GET_SESSION_CALLS
