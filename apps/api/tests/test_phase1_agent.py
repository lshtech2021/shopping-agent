from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_closed_loop_returns_recommendations(mock_shopper):
    created = client.post(
        "/api/v1/sessions",
        json={
            "requirement": "帮我挑一台 5000 元内用于开发的笔记本，内存至少 32G",
            "site": "jd",
        },
    ).json()
    session_id = created["session_id"]

    result = client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"content": "帮我挑一台 5000 元内用于开发的笔记本，内存至少 32G"},
    )
    assert result.status_code == 200
    body = result.json()
    assert body["interrupted"] is False
    assert body["recommendations"]
    assert any("32" in (item["title"] + item["reason"]) for item in body["recommendations"])
    assert "配件" not in body["recommendations"][0]["title"]
    assert body["execution_logs"]
    assert any(str(item.get("stage", "")).startswith("agent.") for item in body["execution_logs"])

    detail = client.get(f"/api/v1/sessions/{session_id}").json()
    assert detail["products"]
    assert detail["recommendations"]
    assert detail["status"] == "done"


def test_rate_limited_search_interrupts_and_resume_works(mock_shopper):
    created = client.post(
        "/api/v1/sessions",
        json={"requirement": "笔记本 预算5000", "site": "jd"},
    ).json()
    session_id = created["session_id"]

    blocked = client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"content": "笔记本 预算5000 rate_limit_me"},
    ).json()
    assert blocked["interrupted"] is True
    assert blocked["interrupt_reason"] == "rate_limited"

    resumed = client.post(
        f"/api/v1/sessions/{session_id}/resume",
        json={"note": "continue"},
    ).json()
    assert resumed["interrupted"] is False
    assert resumed["recommendations"]
