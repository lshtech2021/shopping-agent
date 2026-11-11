"""Optional fail-closed auth for session APIs."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

client = TestClient(app)


def test_session_gets_open_when_auth_not_required(monkeypatch):
    monkeypatch.setattr(settings, "auth_required", False)
    monkeypatch.setattr(settings, "extension_token", "")
    created = client.post(
        "/api/v1/sessions",
        json={"requirement": "笔记本", "site": "jd"},
    ).json()
    sid = created["session_id"]
    assert client.get("/api/v1/sessions").status_code == 200
    assert client.get(f"/api/v1/sessions/{sid}").status_code == 200


def test_auth_required_protects_session_reads(monkeypatch):
    monkeypatch.setattr(settings, "auth_required", True)
    monkeypatch.setattr(settings, "extension_token", "secret")

    denied_list = client.get("/api/v1/sessions")
    assert denied_list.status_code == 403

    created = client.post(
        "/api/v1/sessions",
        json={"requirement": "笔记本", "site": "jd"},
        headers={"X-Extension-Token": "secret"},
    )
    assert created.status_code == 200
    sid = created.json()["session_id"]

    denied_detail = client.get(f"/api/v1/sessions/{sid}")
    assert denied_detail.status_code == 403

    allowed = client.get(
        f"/api/v1/sessions/{sid}",
        headers={"X-Extension-Token": "secret"},
    )
    assert allowed.status_code == 200


def test_auth_required_with_empty_token_rejects_all(monkeypatch):
    monkeypatch.setattr(settings, "auth_required", True)
    monkeypatch.setattr(settings, "extension_token", "")
    assert (
        client.post(
            "/api/v1/sessions",
            json={"requirement": "笔记本", "site": "jd"},
        ).status_code
        == 403
    )
