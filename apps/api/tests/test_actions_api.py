from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_action_api_create_next_and_idempotent_observation():
    created = client.post(
        "/api/v1/runs/run-api/actions",
        json={"tool": "search", "args": {"site": "jd", "query": "x"}},
    )
    assert created.status_code == 200
    action = created.json()
    assert action["action_id"]

    duplicate = client.post(
        "/api/v1/runs/run-api/actions",
        json={"tool": "search", "args": {"site": "jd", "query": "x"}},
    )
    assert duplicate.json()["action_id"] == action["action_id"]

    pending = client.get("/api/v1/runs/run-api/actions/next")
    assert pending.json()["action_id"] == action["action_id"]

    first = client.post(
        f"/api/v1/actions/{action['action_id']}/observations",
        json={"payload": {"status": "ok", "items": [1]}},
    )
    second = client.post(
        f"/api/v1/actions/{action['action_id']}/observations",
        json={"payload": {"status": "blocked"}},
    )
    assert first.status_code == 200
    assert first.json() == second.json()
    assert first.json()["status"] == "ok"
    assert client.get("/api/v1/runs/run-api/actions/next").json() is None


def test_session_tools_exposes_mcp_style_json_schema():
    created = client.post(
        "/api/v1/sessions",
        json={"requirement": "笔记本", "site": "jd"},
    ).json()
    response = client.get(
        f"/api/v1/sessions/{created['session_id']}/tools"
    )
    assert response.status_code == 200
    tools = response.json()
    assert any(tool["name"] == "search" for tool in tools)
    search = next(tool for tool in tools if tool["name"] == "search")
    assert search["inputSchema"]["type"] == "object"
