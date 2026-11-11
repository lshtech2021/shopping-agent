from fastapi.testclient import TestClient

from app.main import app
from app.store import store

client = TestClient(app)


def test_duplicate_message_with_idempotency_key_returns_original_response(
    mock_shopper,
):
    created = client.post(
        "/api/v1/sessions",
        json={"requirement": "笔记本 预算5000", "site": "jd"},
    ).json()
    session_id = created["session_id"]

    first = client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"content": "笔记本 预算5000"},
        headers={"Idempotency-Key": "message-1"},
    ).json()
    turn_after_first = store.get(session_id).turn

    second = client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"content": "笔记本 预算5000"},
        headers={"Idempotency-Key": "message-1"},
    ).json()

    assert second == first
    assert store.get(session_id).turn == turn_after_first


def test_client_message_id_is_an_idempotency_alias(mock_shopper):
    created = client.post(
        "/api/v1/sessions",
        json={"requirement": "笔记本 预算5000", "site": "jd"},
    ).json()
    session_id = created["session_id"]

    first = client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"content": "笔记本 预算5000", "client_message_id": "cm-1"},
    ).json()
    second = client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"content": "笔记本 预算5000", "client_message_id": "cm-1"},
    ).json()

    assert second == first


def test_resume_is_rejected_for_a_done_session(mock_shopper):
    created = client.post(
        "/api/v1/sessions",
        json={"requirement": "笔记本 预算5000", "site": "jd"},
    ).json()
    session_id = created["session_id"]
    client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"content": "笔记本 预算5000"},
    )
    assert store.get(session_id).status == "done"

    response = client.post(
        f"/api/v1/sessions/{session_id}/resume",
        json={"note": "continue"},
    )

    assert response.status_code == 409
