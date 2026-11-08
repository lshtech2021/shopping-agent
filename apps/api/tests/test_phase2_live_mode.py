from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

client = TestClient(app)


def test_live_mode_without_extension_blocks_browse(monkeypatch):
    monkeypatch.setattr(settings, "mock_browser", False)
    created = client.post(
        "/api/v1/sessions",
        json={"requirement": "开发笔记本 5000", "sites": ["jd"]},
    ).json()
    body = client.post(
        f"/api/v1/sessions/{created['session_id']}/messages",
        json={"content": "开发笔记本 预算五千"},
    ).json()
    assert body["interrupted"] is True
    assert body["interrupt_reason"] in {
        "blocked",
        "rate_limited",
        "captcha",
        "login_required",
    }
