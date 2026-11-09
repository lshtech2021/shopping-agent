"""Optional ConnectionPool wiring for RunRepository (SA_PG_POOL_SIZE)."""
from __future__ import annotations

from app.config import settings
from app.store_pg import RunRepository


def test_pg_pool_size_defaults_to_zero():
    assert settings.pg_pool_size == 0


def test_memory_repo_never_opens_pool(monkeypatch):
    monkeypatch.setattr(settings, "pg_pool_size", 4)
    opened = {"n": 0}

    class BoomPool:
        def __init__(self, *args, **kwargs):
            opened["n"] += 1
            raise AssertionError("pool must not open for memory store")

    monkeypatch.setattr("app.store_pg.ConnectionPool", BoomPool)
    repo = RunRepository("memory")
    assert repo.persistent is False
    assert repo._pool is None
    assert opened["n"] == 0


def test_pg_repo_opens_pool_when_size_positive(monkeypatch):
    created = {}

    class FakePool:
        def __init__(self, conninfo, **kwargs):
            created["conninfo"] = conninfo
            created["kwargs"] = kwargs
            created["max_size"] = kwargs.get("max_size")

        def close(self):
            pass

    monkeypatch.setattr(settings, "pg_pool_size", 3)
    monkeypatch.setattr(settings, "auto_create_schema", False)
    monkeypatch.setattr("app.store_pg.ConnectionPool", FakePool)

    class FakeRepo(RunRepository):
        _memory_instance = None

        def __new__(cls, database_url: str | None = "memory"):
            return object.__new__(cls)

    repo = FakeRepo("postgresql://shopping:shopping@localhost:5432/shopping_agent")
    assert repo._pool is not None
    assert created["max_size"] == 3
    assert "postgresql://" in created["conninfo"]
    assert repo._conn is None


def test_pg_repo_uses_single_conn_when_pool_size_zero(monkeypatch):
    monkeypatch.setattr(settings, "pg_pool_size", 0)
    monkeypatch.setattr(settings, "auto_create_schema", False)

    class BoomPool:
        def __init__(self, *args, **kwargs):
            raise AssertionError("pool must stay off when size is 0")

    monkeypatch.setattr("app.store_pg.ConnectionPool", BoomPool)

    class FakeRepo(RunRepository):
        _memory_instance = None

        def __new__(cls, database_url: str | None = "memory"):
            return object.__new__(cls)

        def _connection(self):
            raise AssertionError("lazy connect only on use")

    repo = FakeRepo("postgresql://example/db")
    assert repo._pool is None


def test_pool_pins_connection_for_session_advisory_lock(monkeypatch):
    class FakeConn:
        def __init__(self):
            self.ops: list[str] = []

        def execute(self, sql, params=None):
            self.ops.append(sql.strip().split()[1])  # try_advisory / advisory_unlock
            class Row:
                def fetchone(self_inner):
                    return {"locked": True}

            return Row()

    held: list[FakeConn] = []
    returned: list[FakeConn] = []

    class FakePool:
        def __init__(self, *args, **kwargs):
            pass

        def getconn(self):
            conn = FakeConn()
            held.append(conn)
            return conn

        def putconn(self, conn):
            returned.append(conn)

        def close(self):
            pass

    monkeypatch.setattr(settings, "pg_pool_size", 2)
    monkeypatch.setattr(settings, "auto_create_schema", False)
    monkeypatch.setattr("app.store_pg.ConnectionPool", FakePool)

    class FakeRepo(RunRepository):
        _memory_instance = None

        def __new__(cls, database_url: str | None = "memory"):
            return object.__new__(cls)

    repo = FakeRepo("postgresql://example/db")
    assert repo.try_turn_lock("run-a") is True
    assert list(repo._held_locks) == ["run-a"]
    assert returned == []
    repo.release_turn_lock("run-a")
    assert repo._held_locks == {}
    assert returned == held
