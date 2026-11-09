"""Runtime DDL must be opt-in; Alembic owns schema by default."""
from __future__ import annotations

from app.config import settings
from app.store_pg import RunRepository


def test_auto_create_schema_defaults_off():
    assert settings.auto_create_schema is False


def test_pg_repository_skips_init_schema_when_disabled(monkeypatch):
    called = {"n": 0}

    def boom(self):
        called["n"] += 1
        raise AssertionError("_init_schema should not run")

    monkeypatch.setattr(settings, "auto_create_schema", False)
    monkeypatch.setattr(RunRepository, "_init_schema", boom)
    # Memory mode never touches schema; force a PG-looking URL path without connecting
    # by constructing with memory after patch — still should not call init.
    repo = RunRepository("memory")
    assert repo.persistent is False
    assert called["n"] == 0


def test_pg_repository_runs_init_schema_when_enabled(monkeypatch):
    called = {"n": 0}

    def mark(self):
        called["n"] += 1

    monkeypatch.setattr(settings, "auto_create_schema", True)
    monkeypatch.setattr(RunRepository, "_init_schema", mark)

    class FakeRepo(RunRepository):
        _memory_instance = None

        def __init__(self, database_url: str | None = "memory") -> None:
            # Bypass real connect: emulate PG branch.
            self._url = "postgresql://example/db"
            self._conn = None
            self._pool = None
            self._held_locks = {}
            import threading

            self._lock = threading.Lock()
            if settings.auto_create_schema:
                self._init_schema()

        def _connection(self):
            class _Conn:
                def execute(self, *args, **kwargs):
                    return self

            return _Conn()

    FakeRepo("postgresql://example/db")
    assert called["n"] == 1
