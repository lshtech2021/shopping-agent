from fastapi.testclient import TestClient

from app.main import app
from app.store import SessionStore, store

client = TestClient(app)


def test_list_sessions_and_persistence(tmp_path, monkeypatch):
    from app.config import settings

    db = tmp_path / "persist.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{db.as_posix()}")
    store.reset(settings.database_url)

    created = client.post(
        "/api/v1/sessions",
        json={"requirement": "开发笔记本 5000", "sites": ["jd"]},
    ).json()
    sid = created["session_id"]
    client.post(
        f"/api/v1/sessions/{sid}/messages",
        json={"content": "开发笔记本 预算五千"},
    )

    listed = client.get("/api/v1/sessions").json()
    assert any(item["session_id"] == sid for item in listed)

    # Reload from SQLite
    reloaded = SessionStore(settings.database_url)
    assert reloaded.get(sid) is not None
    assert reloaded.get(sid).recommendations


def test_console_static_mounted():
    response = client.get("/console/")
    assert response.status_code == 200
    assert "只读控制台" in response.text
