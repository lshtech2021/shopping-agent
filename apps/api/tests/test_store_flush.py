"""SessionStore batches durable writes until flush / durable touch."""
from __future__ import annotations

from app.store import SessionStore


def test_soft_touch_does_not_persist_until_flush():
    store = SessionStore("memory")
    session = store.create("笔记本", ["jd"])
    version_after_create = session.version

    session.products["p1"] = {"product_id": "p1", "title": "A"}
    store.touch(session)  # soft by default
    assert session.id in store._dirty

    durable = store._repo.get(session.id)
    assert durable is not None
    assert durable.get("products") in (None, {})
    assert int(durable.get("version") or 0) == version_after_create

    store.flush(session)
    durable = store._repo.get(session.id)
    assert durable is not None
    assert "p1" in (durable.get("products") or {})
    assert session.id not in store._dirty


def test_durable_touch_persists_immediately():
    store = SessionStore("memory")
    session = store.create("笔记本", ["jd"])
    session.products["p2"] = {"product_id": "p2", "title": "B"}
    store.touch(session, durable=True)
    durable = store._repo.get(session.id)
    assert durable is not None
    assert "p2" in (durable.get("products") or {})
    assert session.id not in store._dirty
