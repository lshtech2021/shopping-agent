"""Run repository listing should support a hard limit."""
from __future__ import annotations

from app.store_pg import RunRepository
from test_store_pg import _run


def test_list_runs_respects_limit():
    repo = RunRepository("memory")
    for index in range(5):
        repo.create(
            _run(
                f"run-{index}",
                updated_at=f"2026-11-09T00:00:0{index}+00:00",
            )
        )
    listed = repo.list_runs(limit=2)
    assert len(listed) == 2
    assert [item["id"] for item in listed] == ["run-4", "run-3"]
