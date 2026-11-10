"""Durable session writes from async agent paths should use worker threads."""
from __future__ import annotations

import asyncio
import threading

from app.agent_runner import durable_touch
from app.store import store


def test_durable_touch_runs_store_write_off_event_loop(monkeypatch):
    session = store.create("笔记本", ["jd"])
    main_id = threading.get_ident()
    seen: dict[str, int] = {}

    real_touch = store.touch

    def tracking_touch(sess, *, durable: bool = False, **kwargs):
        if durable:
            seen["thread"] = threading.get_ident()
        return real_touch(sess, durable=durable, **kwargs)

    monkeypatch.setattr(store, "touch", tracking_touch)
    asyncio.run(durable_touch(session))
    assert seen["thread"] != main_id
