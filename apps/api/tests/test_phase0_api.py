from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_create_session_and_echo_message():
    created = client.post(
        "/api/v1/sessions",
        json={"requirement": "帮我挑笔记本", "sites": ["jd"]},
    )
    assert created.status_code == 200
    body = created.json()
    assert body["session_id"]
    assert body["sites"] == ["jd"]

    msg = client.post(
        f"/api/v1/sessions/{body['session_id']}/messages",
        json={"content": "预算五千，开发笔记本"},
    )
    assert msg.status_code == 200
    data = msg.json()
    assert data["interrupted"] is False
    assert data["recommendations"] or "选品" in data["reply"]


def test_extension_ws_ping_pong():
    with client.websocket_connect("/ws/extension") as ws:
        ws.send_json({"type": "ping", "ts": 1})
        reply = ws.receive_json()
        assert reply["type"] == "pong"
        assert reply["ts"] == 1
