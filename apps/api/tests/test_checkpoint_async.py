"""PgCheckpointStore must not block the event loop on sync repo I/O."""
from __future__ import annotations

import asyncio
import threading

from langchain_core.messages import HumanMessage

from app.agent.checkpoint import PgCheckpointStore


def test_pg_checkpoint_save_load_delete_use_worker_thread(monkeypatch):
    store = PgCheckpointStore("memory")
    main_id = threading.get_ident()
    seen: dict[str, int] = {}

    real_save = store.repo.save_checkpoint
    real_load = store.repo.load_checkpoint
    real_delete = store.repo.delete_checkpoint

    def save_checkpoint(run_id, state, updated_at):
        seen["save"] = threading.get_ident()
        return real_save(run_id, state, updated_at)

    def load_checkpoint(run_id):
        seen["load"] = threading.get_ident()
        return real_load(run_id)

    def delete_checkpoint(run_id):
        seen["delete"] = threading.get_ident()
        return real_delete(run_id)

    monkeypatch.setattr(store.repo, "save_checkpoint", save_checkpoint)
    monkeypatch.setattr(store.repo, "load_checkpoint", load_checkpoint)
    monkeypatch.setattr(store.repo, "delete_checkpoint", delete_checkpoint)

    state = {
        "run_id": "run-async",
        "messages": [HumanMessage(content="hi")],
        "plan": None,
        "step": 1,
        "turn": 1,
        "phase": "executing",
        "tool_results": [],
        "pending_user": None,
        "error": None,
    }

    async def roundtrip():
        await store.save("run-async", state)
        loaded = await store.load("run-async")
        assert loaded is not None
        await store.delete("run-async")
        assert await store.load("run-async") is None

    asyncio.run(roundtrip())
    assert seen["save"] != main_id
    assert seen["load"] != main_id
    assert seen["delete"] != main_id
