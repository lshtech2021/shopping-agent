
"""Polling GET must not clobber the live in-memory session mid-turn."""
from __future__ import annotations

from app.agent_runner import durable_touch
from app.store import SessionStore
import asyncio


def test_snapshot_prefers_live_working_copy_over_stale_db():
    store = SessionStore("memory")
    session = store.create("笔记本", "jd")
    store.touch(session, durable=True)
    session.products["p1"] = {"product_id": "p1", "title": "live"}
    session.status = "running"

    # Simulate a concurrent DB writer with an older document.
    stale = store._repo.get(session.id)
    assert stale is not None
    stale["products"] = {}
    stale["status"] = "idle"
    store._repo.update(session.id, stale, expected_version=session.version)
    # Keep the live object as the working set (as the agent does).
    store._sessions[session.id] = session

    snap = store.snapshot(session.id)
    assert snap is not None
    assert "p1" in snap.products
    assert store.get(session.id) is session


def test_durable_touch_retries_version_conflict(monkeypatch):
    store = SessionStore("memory")
    session = store.create("笔记本", "jd")
    store.touch(session, durable=True)
    expected = session.version

    # Concurrent bump while the agent still holds expected.
    latest = store._repo.get(session.id)
    assert latest is not None
    store._repo.update(session.id, latest, expected_version=expected)
    session.products["kept"] = {"product_id": "kept"}
    session.version = expected

    store.touch(session, durable=True, retry_conflict=True)

    saved = store._repo.get(session.id)
    assert saved is not None
    assert "kept" in (saved.get("products") or {})
