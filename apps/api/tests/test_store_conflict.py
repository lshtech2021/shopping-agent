"""SessionStore must not clobber newer durable state on VersionConflict."""
from __future__ import annotations

import pytest

from app.store import SessionStore
from app.store_pg import VersionConflict


def test_version_conflict_does_not_overwrite_newer_products():
    store = SessionStore("memory")
    session = store.create("笔记本", "jd")
    assert session.version == 1

    # Concurrent writer lands newer products at version 2.
    latest = store._repo.get(session.id)
    assert latest is not None
    latest["products"] = {
        "keep": {
            "product_id": "keep",
            "title": "Should survive",
            "source": "jd",
        }
    }
    store._repo.update(session.id, latest, expected_version=1)

    # Stale in-memory session still at version 1 with empty products.
    session.products = {}
    session.recommendations = [{"product_id": "stale"}]
    assert session.version == 1

    with pytest.raises(VersionConflict):
        store.touch(session, durable=True)

    stored = store._repo.get(session.id)
    assert stored is not None
    assert "keep" in (stored.get("products") or {})
    assert stored.get("recommendations") in (None, [], {})
    # Cache should reflect durable winner, not the stale blob.
    refreshed = store.get(session.id)
    assert refreshed is not None
    assert "keep" in refreshed.products
