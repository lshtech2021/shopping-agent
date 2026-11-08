import os

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store import SessionStore, store

client = TestClient(app)


def test_list_sessions_memory():
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
    assert store.get(sid) is not None
    assert store.get(sid).recommendations


def test_console_static_mounted():
    response = client.get("/console/")
    assert response.status_code == 200
    assert "只读控制台" in response.text


@pytest.mark.skipif(
    not os.getenv("SA_TEST_DATABASE_URL"),
    reason="Set SA_TEST_DATABASE_URL to run PostgreSQL persistence tests",
)
def test_postgres_persistence(monkeypatch):
    from app.config import settings

    url = os.environ["SA_TEST_DATABASE_URL"]
    monkeypatch.setattr(settings, "database_url", url)
    store.reset(url)

    # Isolate rows for this test run
    prefix = "pgtest-"
    created = client.post(
        "/api/v1/sessions",
        json={"requirement": f"{prefix}开发笔记本 5000", "sites": ["jd"]},
    ).json()
    sid = created["session_id"]
    client.post(
        f"/api/v1/sessions/{sid}/messages",
        json={"content": "开发笔记本 预算五千"},
    )

    reloaded = SessionStore(url)
    assert reloaded.get(sid) is not None
    assert reloaded.get(sid).recommendations
