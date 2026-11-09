"""Durable run repository and Phase 4 table schema."""
from __future__ import annotations

import copy
import json
import threading
from typing import Any, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


class VersionConflict(RuntimeError):
    """The run changed after the caller read it."""


def _pg_url(database_url: str | None) -> str | None:
    raw = (database_url or "").strip()
    if not raw or raw == "memory":
        return None
    if raw.startswith("postgres://"):
        return "postgresql://" + raw[len("postgres://") :]
    if raw.startswith("postgresql+psycopg://"):
        return raw.replace("postgresql+psycopg://", "postgresql://", 1)
    return raw


_JSON_COLUMNS = {
    "sites",
    "requirements",
    "plan",
    "messages",
    "products",
    "recommendations",
    "quotas",
    "searched_sites",
    "pending_questions",
    "blocked_sites",
    "execution_logs",
}
_ALL_COLUMNS = [
    "id",
    "requirement",
    "sites",
    "status",
    "requirements",
    "plan",
    "messages",
    "products",
    "recommendations",
    "quotas",
    "searched_sites",
    "interrupt_reason",
    "pending_questions",
    "blocked_sites",
    "execution_logs",
    "turn",
    "created_at",
    "updated_at",
    "version",
]
_UPDATE_COLUMNS = [
    column for column in _ALL_COLUMNS if column not in {"id", "created_at"}
]


class RunRepository:
    _memory_instance: "RunRepository | None" = None
    _memory_runs: dict[str, dict[str, Any]] = {}

    def __new__(cls, database_url: str | None = "memory"):
        if _pg_url(database_url) is None:
            if cls._memory_instance is None:
                cls._memory_instance = super().__new__(cls)
            return cls._memory_instance
        return super().__new__(cls)

    def __init__(self, database_url: str | None = "memory") -> None:
        url = _pg_url(database_url)
        self._url = url
        self._conn: Optional[psycopg.Connection] = None
        self._lock = threading.Lock()
        if url is not None:
            self._init_schema()

    @property
    def persistent(self) -> bool:
        return self._url is not None

    def clear(self) -> None:
        if self._url is None:
            self._memory_runs.clear()
            return
        with self._conn_lock():
            conn = self._connection()
            conn.execute(
                "TRUNCATE runs, actions, observations, run_events, "
                "products, candidates, messages"
            )

    def _conn_lock(self) -> threading.Lock:
        return self._lock

    def _connection(self) -> psycopg.Connection:
        assert self._url is not None
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(
                self._url, row_factory=dict_row, autocommit=True
            )
        return self._conn

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._connection()
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    requirement TEXT NOT NULL,
                    sites JSONB NOT NULL,
                    status TEXT NOT NULL,
                    requirements JSONB NOT NULL,
                    plan JSONB,
                    messages JSONB NOT NULL,
                    products JSONB NOT NULL,
                    recommendations JSONB NOT NULL,
                    quotas JSONB NOT NULL,
                    searched_sites JSONB NOT NULL,
                    interrupt_reason TEXT,
                    pending_questions JSONB NOT NULL,
                    blocked_sites JSONB NOT NULL,
                    execution_logs JSONB NOT NULL,
                    turn INT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    version BIGINT NOT NULL DEFAULT 1,
                    lease_owner TEXT,
                    lease_expires_at TIMESTAMPTZ,
                    heartbeat_at TIMESTAMPTZ
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS actions (
                    action_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    args JSONB NOT NULL,
                    status TEXT NOT NULL,
                    result JSONB,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS observations (
                    action_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    payload JSONB NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS run_events (
                    id BIGSERIAL PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    event JSONB NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS products (
                    run_id TEXT NOT NULL,
                    product_id TEXT NOT NULL,
                    payload JSONB NOT NULL,
                    PRIMARY KEY (run_id, product_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS candidates (
                    run_id TEXT NOT NULL,
                    product_id TEXT NOT NULL,
                    payload JSONB NOT NULL,
                    PRIMARY KEY (run_id, product_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id BIGSERIAL PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    payload JSONB NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def _params(self, record: dict[str, Any]) -> dict[str, Any]:
        return {
            column: (
                Jsonb(record.get(column))
                if column in _JSON_COLUMNS
                else record.get(column)
            )
            for column in _ALL_COLUMNS
        }

    def create(self, record: dict[str, Any]) -> dict[str, Any]:
        record = copy.deepcopy(record)
        record["version"] = max(1, int(record.get("version") or 1))
        if self._url is None:
            if record["id"] in self._memory_runs:
                raise ValueError(f"run already exists: {record['id']}")
            self._memory_runs[record["id"]] = record
            return copy.deepcopy(record)
        columns = ", ".join(_ALL_COLUMNS)
        placeholders = ", ".join(f"%({column})s" for column in _ALL_COLUMNS)
        with self._lock:
            row = self._connection().execute(
                f"INSERT INTO runs ({columns}) VALUES ({placeholders}) RETURNING *",
                self._params(record),
            ).fetchone()
        return self._decode(row)

    def get(self, run_id: str) -> dict[str, Any] | None:
        if self._url is None:
            record = self._memory_runs.get(run_id)
            return copy.deepcopy(record) if record else None
        with self._lock:
            row = self._connection().execute(
                "SELECT * FROM runs WHERE id = %s", (run_id,)
            ).fetchone()
        return self._decode(row) if row else None

    def update(
        self,
        run_id: str,
        patch: dict[str, Any],
        *,
        expected_version: int,
    ) -> dict[str, Any]:
        if self._url is None:
            record = self._memory_runs.get(run_id)
            if record is None:
                raise KeyError(run_id)
            if int(record["version"]) != expected_version:
                raise VersionConflict(
                    f"run {run_id} version changed "
                    f"(expected {expected_version}, got {record['version']})"
                )
            next_record = copy.deepcopy(record)
            for key, value in patch.items():
                if key in _UPDATE_COLUMNS:
                    next_record[key] = copy.deepcopy(value)
            next_record["version"] = expected_version + 1
            self._memory_runs[run_id] = next_record
            return copy.deepcopy(next_record)

        assignments = ", ".join(
            f"{column} = %({column})s"
            for column in _UPDATE_COLUMNS
            if column != "version"
        )
        params = {
            column: Jsonb(patch[column]) if column in _JSON_COLUMNS else patch[column]
            for column in _UPDATE_COLUMNS
            if column in patch and column != "version"
        }
        params["id"] = run_id
        params["expected_version"] = expected_version
        with self._lock:
            row = self._connection().execute(
                f"UPDATE runs SET {assignments}, version = version + 1 "
                "WHERE id = %(id)s AND version = %(expected_version)s "
                "RETURNING *",
                params,
            ).fetchone()
        if row is None:
            raise VersionConflict(
                f"run {run_id} version changed (expected {expected_version})"
            )
        return self._decode(row)

    def list_runs(self) -> list[dict[str, Any]]:
        if self._url is None:
            return [
                copy.deepcopy(record)
                for record in sorted(
                    self._memory_runs.values(),
                    key=lambda item: item.get("updated_at") or "",
                    reverse=True,
                )
            ]
        with self._lock:
            rows = self._connection().execute(
                "SELECT * FROM runs ORDER BY updated_at DESC"
            ).fetchall()
        return [self._decode(row) for row in rows]

    def delete(self, run_id: str) -> None:
        if self._url is None:
            self._memory_runs.pop(run_id, None)
            return
        with self._lock:
            self._connection().execute(
                "DELETE FROM runs WHERE id = %s", (run_id,)
            )

    @staticmethod
    def _decode(row: dict[str, Any] | None) -> dict[str, Any]:
        if row is None:
            return {}
        record = dict(row)
        for column in _JSON_COLUMNS:
            value = record.get(column)
            if isinstance(value, str):
                record[column] = json.loads(value)
        return record
