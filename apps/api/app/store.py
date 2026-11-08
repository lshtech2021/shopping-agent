"""Session store with optional PostgreSQL JSON persistence."""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import psycopg
from psycopg.rows import dict_row

from app.config import settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Session:
    id: str
    requirement: str
    sites: list[str]
    status: str = "idle"
    requirements: dict[str, Any] = field(default_factory=dict)
    messages: list[dict[str, Any]] = field(default_factory=list)
    products: dict[str, dict[str, Any]] = field(default_factory=dict)
    recommendations: list[dict[str, Any]] = field(default_factory=list)
    quotas: dict[str, dict[str, int]] = field(default_factory=dict)
    searched_sites: list[str] = field(default_factory=list)
    interrupt_reason: Optional[str] = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Session":
        return cls(
            id=str(data["id"]),
            requirement=str(data.get("requirement") or ""),
            sites=list(data.get("sites") or []),
            status=str(data.get("status") or "idle"),
            requirements=dict(data.get("requirements") or {}),
            messages=list(data.get("messages") or []),
            products=dict(data.get("products") or {}),
            recommendations=list(data.get("recommendations") or []),
            quotas=dict(data.get("quotas") or {}),
            searched_sites=list(data.get("searched_sites") or []),
            interrupt_reason=data.get("interrupt_reason"),
            created_at=str(data.get("created_at") or _now()),
            updated_at=str(data.get("updated_at") or _now()),
        )


def _normalize_database_url(database_url: str) -> Optional[str]:
    url = (database_url or "").strip()
    if not url or url in {"memory", "sqlite:///:memory:"}:
        return None
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://") or url.startswith("postgresql+psycopg://"):
        return url.replace("postgresql+psycopg://", "postgresql://", 1)
    raise ValueError(
        f"Unsupported database URL (expected postgresql:// or memory): {database_url!r}"
    )


class SessionStore:
    def __init__(self, database_url: str | None = None) -> None:
        self._sessions: dict[str, Session] = {}
        raw = database_url if database_url is not None else settings.database_url
        self._database_url = raw
        self._pg_url = _normalize_database_url(raw)
        self._init_db()
        self._load_all()

    def reset(self, database_url: str | None = None) -> None:
        self._sessions.clear()
        if database_url is not None:
            self._database_url = database_url
            self._pg_url = _normalize_database_url(database_url)
        self._init_db()
        self._load_all()

    def _connect(self) -> Optional[psycopg.Connection]:
        if self._pg_url is None:
            return None
        return psycopg.connect(self._pg_url, row_factory=dict_row)

    def _init_db(self) -> None:
        conn = self._connect()
        if conn is None:
            return
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS sessions (
                        id TEXT PRIMARY KEY,
                        updated_at TEXT NOT NULL,
                        payload JSONB NOT NULL
                    )
                    """
                )
            conn.commit()
        finally:
            conn.close()

    def _load_all(self) -> None:
        conn = self._connect()
        if conn is None:
            return
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT payload FROM sessions ORDER BY updated_at DESC"
                )
                rows = cur.fetchall()
            for row in rows:
                payload = row["payload"]
                data = payload if isinstance(payload, dict) else json.loads(payload)
                session = Session.from_dict(data)
                self._sessions[session.id] = session
        finally:
            conn.close()

    def _persist(self, session: Session) -> None:
        conn = self._connect()
        if conn is None:
            return
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO sessions(id, updated_at, payload)
                    VALUES(%s, %s, %s::jsonb)
                    ON CONFLICT(id) DO UPDATE SET
                        updated_at=EXCLUDED.updated_at,
                        payload=EXCLUDED.payload
                    """,
                    (
                        session.id,
                        session.updated_at,
                        json.dumps(session.to_dict(), ensure_ascii=False),
                    ),
                )
            conn.commit()
        finally:
            conn.close()

    def create(self, requirement: str, sites: list[str]) -> Session:
        session = Session(
            id=uuid.uuid4().hex,
            requirement=requirement.strip(),
            sites=list(sites),
            requirements={
                "raw": requirement.strip(),
                "budget_cny": None,
                "must_have": [],
                "preferences": [],
                "category": None,
                "use_case": None,
            },
            status="idle",
        )
        session.messages.append(
            {"role": "user", "content": requirement.strip(), "ts": _now()}
        )
        self._sessions[session.id] = session
        self._persist(session)
        return session

    def get(self, session_id: str) -> Optional[Session]:
        return self._sessions.get(session_id)

    def touch(self, session: Session) -> None:
        session.updated_at = _now()
        self._sessions[session.id] = session
        self._persist(session)

    def list_ids(self) -> list[str]:
        return [
            s.id
            for s in sorted(
                self._sessions.values(),
                key=lambda item: item.updated_at,
                reverse=True,
            )
        ]

    def list_summaries(self, limit: int = 50) -> list[dict[str, Any]]:
        sessions = sorted(
            self._sessions.values(),
            key=lambda item: item.updated_at,
            reverse=True,
        )[: max(1, limit)]
        return [
            {
                "session_id": s.id,
                "requirement": s.requirement,
                "sites": s.sites,
                "status": s.status,
                "product_count": len(s.products),
                "recommendation_count": len(s.recommendations),
                "updated_at": s.updated_at,
                "created_at": s.created_at,
            }
            for s in sessions
        ]


# Import-safe default; FastAPI lifespan binds the configured PostgreSQL URL.
store = SessionStore("memory")
