from __future__ import annotations

import os

import pytest

from app.store_pg import RunRepository, VersionConflict


def _run(run_id: str = "run-1", **overrides) -> dict:
    record = {
        "id": run_id,
        "requirement": "开发笔记本",
        "sites": ["jd"],
        "status": "idle",
        "requirements": {"budget_cny": 5000},
        "plan": None,
        "messages": [],
        "products": {},
        "recommendations": [],
        "quotas": {},
        "searched_sites": [],
        "interrupt_reason": None,
        "pending_questions": [],
        "blocked_sites": {},
        "execution_logs": [],
        "turn": 0,
        "created_at": "2026-11-09T00:00:00+00:00",
        "updated_at": "2026-11-09T00:00:00+00:00",
        "version": 1,
    }
    record.update(overrides)
    return record


def test_run_repository_round_trip_and_version_conflict():
    repo = RunRepository("memory")
    created = repo.create(_run())
    assert created["version"] == 1

    first = repo.update(
        "run-1", {"status": "running"}, expected_version=1
    )
    assert first["status"] == "running"
    assert first["version"] == 2

    with pytest.raises(VersionConflict):
        repo.update("run-1", {"status": "stale"}, expected_version=1)

    assert repo.get("run-1")["status"] == "running"


def test_two_repository_instances_share_memory_version_state():
    repo_a = RunRepository("memory")
    repo_b = RunRepository("memory")
    assert repo_a is repo_b

    repo_a.create(_run("run-2"))
    updated = repo_b.update(
        "run-2", {"status": "done"}, expected_version=1
    )
    assert updated["version"] == 2


@pytest.mark.skipif(
    not os.getenv("SA_TEST_DATABASE_URL"),
    reason="Set SA_TEST_DATABASE_URL to run PostgreSQL repository tests",
)
def test_postgres_repository_round_trip(monkeypatch):
    from app.config import settings

    # Integration DB may not have been migrated in CI; allow one-shot DDL here.
    monkeypatch.setattr(settings, "auto_create_schema", True)
    url = os.environ["SA_TEST_DATABASE_URL"]
    repo = RunRepository(url)
    repo.delete("pg-run-1")
    repo.create(_run("pg-run-1"))

    updated = repo.update(
        "pg-run-1",
        {"status": "running", "turn": 1},
        expected_version=1,
    )
    assert updated["status"] == "running"
    assert updated["version"] == 2

    reloaded = RunRepository(url)
    assert reloaded.get("pg-run-1")["turn"] == 1
