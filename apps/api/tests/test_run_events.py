"""Append-only run_events cursor for mid-turn progress."""
from __future__ import annotations

from app.store_pg import RunRepository


def test_memory_run_events_append_and_list_after_cursor():
    repo = RunRepository("memory")
    repo.clear()
    first = repo.append_run_event(
        "run-1",
        {"stage": "tool.start", "message": "search"},
        created_at="2026-11-09T00:00:01+00:00",
    )
    second = repo.append_run_event(
        "run-1",
        {"stage": "tool.end", "message": "ok"},
        created_at="2026-11-09T00:00:02+00:00",
    )
    assert first["id"] >= 1
    assert second["id"] > first["id"]

    all_events = repo.list_run_events("run-1")
    assert [item["event"]["stage"] for item in all_events] == [
        "tool.start",
        "tool.end",
    ]

    after = repo.list_run_events("run-1", after_id=first["id"])
    assert len(after) == 1
    assert after[0]["id"] == second["id"]
    assert after[0]["event"]["message"] == "ok"


def test_memory_run_events_are_isolated_per_run():
    repo = RunRepository("memory")
    repo.clear()
    repo.append_run_event("a", {"message": "a1"}, created_at="t1")
    repo.append_run_event("b", {"message": "b1"}, created_at="t2")
    assert [e["event"]["message"] for e in repo.list_run_events("a")] == ["a1"]
    assert [e["event"]["message"] for e in repo.list_run_events("b")] == ["b1"]
