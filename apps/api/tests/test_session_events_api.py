"""Mid-turn event cursor API."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.store import store
from app.trace import log_event


def test_log_event_is_readable_via_events_cursor():
    client = TestClient(app)
    created = client.post(
        "/api/v1/sessions",
        json={"requirement": "笔记本", "sites": ["jd"]},
    ).json()
    sid = created["session_id"]
    session = store.get(sid)
    assert session is not None

    log_event(session, "searching", stage="tool.start", source="test")
    log_event(session, "done", stage="tool.end", source="test")

    first = client.get(f"/api/v1/sessions/{sid}/events").json()
    assert first["session_id"] == sid
    assert len(first["events"]) >= 2
    stages = [row["event"]["stage"] for row in first["events"]]
    assert "tool.start" in stages
    assert "tool.end" in stages

    after = first["next_after"]
    empty = client.get(
        f"/api/v1/sessions/{sid}/events", params={"after": after}
    ).json()
    assert empty["events"] == []
    assert empty["next_after"] == after


def test_events_404_for_unknown_session():
    client = TestClient(app)
    assert client.get("/api/v1/sessions/missing/events").status_code == 404
