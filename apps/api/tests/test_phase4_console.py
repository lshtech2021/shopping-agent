import os

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store import SessionStore, store

client = TestClient(app)


def test_list_sessions_memory(mock_shopper):
    created = client.post(
        "/api/v1/sessions",
        json={"requirement": "开发笔记本 5000", "site": "jd"},
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
    detail = client.get(f"/api/v1/sessions/{sid}").json()
    assert detail["execution_logs"]
    assert any("agent" in (item.get("source") or "") for item in detail["execution_logs"])


def test_console_static_mounted():
    response = client.get("/console/")
    assert response.status_code == 200
    assert "只读控制台" in response.text


def _postgres_ready(url: str) -> bool:
    try:
        import psycopg

        with psycopg.connect(url) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(
    not os.getenv("SA_TEST_DATABASE_URL"),
    reason="Set SA_TEST_DATABASE_URL to run PostgreSQL persistence tests",
)
def test_postgres_persistence(mock_shopper, monkeypatch):
    from app.config import settings

    url = os.environ["SA_TEST_DATABASE_URL"]
    if not _postgres_ready(url):
        pytest.skip(f"PostgreSQL not reachable: {url}")

    monkeypatch.setattr(settings, "database_url", url)
    store.reset(url)

    created = client.post(
        "/api/v1/sessions",
        json={"requirement": "pgtest-开发笔记本 5000", "site": "jd"},
    ).json()
    sid = created["session_id"]
    client.post(
        f"/api/v1/sessions/{sid}/messages",
        json={"content": "开发笔记本 预算五千"},
    )

    reloaded = SessionStore(url)
    assert reloaded.get(sid) is not None
    assert reloaded.get(sid).recommendations
    assert reloaded.get(sid).execution_logs
