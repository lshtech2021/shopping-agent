"""Session working set backed by the durable run repository.

Reads are served from an immutable in-process snapshot; every ``touch`` writes
the versioned run record. Multi-worker locking and leases live in
``store_pg``/the run supervisor.
"""
from __future__ import annotations

import copy
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from app.config import settings

# Warm the in-process working set with recent runs only; older runs lazy-load
# through ``get`` / ``refresh``.
SESSION_WARM_LIMIT = 200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Session:
    id: str
    requirement: str
    sites: list[str]
    status: str = "idle"
    requirements: dict[str, Any] = field(default_factory=dict)
    plan: Optional[dict[str, Any]] = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    products: dict[str, dict[str, Any]] = field(default_factory=dict)
    recommendations: list[dict[str, Any]] = field(default_factory=list)
    quotas: dict[str, dict[str, int]] = field(default_factory=dict)
    searched_sites: list[str] = field(default_factory=list)
    interrupt_reason: Optional[str] = None
    # Structured clarify questions waiting for user answers (cleared once applied).
    pending_questions: list[dict[str, Any]] = field(default_factory=list)
    # site -> risk status (rate_limited / captcha / login_required); cleared on resume.
    blocked_sites: dict[str, str] = field(default_factory=dict)
    execution_logs: list[dict[str, Any]] = field(default_factory=list)
    turn: int = 0
    version: int = 0
    lease_owner: Optional[str] = None
    lease_expires_at: Optional[str] = None
    heartbeat_at: Optional[str] = None
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
            plan=dict(data["plan"]) if data.get("plan") else None,
            messages=list(data.get("messages") or []),
            products=dict(data.get("products") or {}),
            recommendations=list(data.get("recommendations") or []),
            quotas=dict(data.get("quotas") or {}),
            searched_sites=list(data.get("searched_sites") or []),
            interrupt_reason=data.get("interrupt_reason"),
            pending_questions=list(data.get("pending_questions") or []),
            blocked_sites=dict(data.get("blocked_sites") or {}),
            execution_logs=list(data.get("execution_logs") or []),
            turn=int(data.get("turn") or 0),
            version=int(data.get("version") or 0),
            lease_owner=data.get("lease_owner"),
            lease_expires_at=data.get("lease_expires_at"),
            heartbeat_at=data.get("heartbeat_at"),
            created_at=str(data.get("created_at") or _now()),
            updated_at=str(data.get("updated_at") or _now()),
        )


def _normalize_database_url(database_url: str) -> Optional[str]:
    url = (database_url or "").strip()
    if not url or url == "memory":
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
        from app.store_pg import RunRepository

        self._sessions: dict[str, Session] = {}
        self._dirty: set[str] = set()
        raw = database_url if database_url is not None else settings.database_url
        self._database_url = raw
        self._pg_url = _normalize_database_url(raw)
        self._repo = RunRepository(raw)
        self._load_all()

    @property
    def persistent(self) -> bool:
        return self._repo.persistent

    def reset(self, database_url: str | None = None) -> None:
        from app.store_pg import RunRepository

        self._sessions.clear()
        self._dirty.clear()
        if database_url is not None:
            self._database_url = database_url
            self._pg_url = _normalize_database_url(database_url)
        if self._pg_url is None:
            self._repo = RunRepository("memory")
            self._repo.clear()
        else:
            self._repo = RunRepository(self._database_url)
        self._load_all()

    def _load_all(self) -> None:
        for record in self._repo.list_runs(limit=SESSION_WARM_LIMIT):
            session = Session.from_dict(record)
            self._sessions[session.id] = session

    def _persist(self, session: Session) -> None:
        if session.version <= 0:
            record = self._repo.create(session.to_dict())
        else:
            try:
                record = self._repo.update(
                    session.id,
                    session.to_dict(),
                    expected_version=session.version,
                )
            except Exception as exc:
                from app.store_pg import VersionConflict

                if not isinstance(exc, VersionConflict):
                    raise
                # Never force-write a stale full document over a newer version.
                latest = self._repo.get(session.id)
                if latest is not None:
                    winner = Session.from_dict(latest)
                    self._sessions[session.id] = winner
                raise
        session.version = int(record.get("version") or session.version)

    # --- public API ----------------------------------------------------------

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
        session = self._sessions.get(session_id)
        if session is None and self.persistent:
            record = self._repo.get(session_id)
            if record is not None:
                session = Session.from_dict(record)
                self._sessions[session_id] = session
        return session

    def refresh(self, session_id: str) -> Optional[Session]:
        if not self.persistent:
            return self.get(session_id)
        record = self._repo.get(session_id)
        if record is None:
            self._sessions.pop(session_id, None)
            return None
        session = Session.from_dict(record)
        self._sessions[session_id] = session
        return session

    def snapshot(self, session_id: str) -> Optional[Session]:
        session = self.refresh(session_id)
        if session is None:
            return None
        return Session.from_dict(copy.deepcopy(session.to_dict()))

    def acquire_lease(
        self, run_id: str, owner: str, expires_at: str
    ) -> bool:
        return self._repo.acquire_lease(run_id, owner, expires_at)

    def release_lease(self, run_id: str) -> None:
        session = self.get(run_id)
        if session is None:
            return
        session.lease_owner = None
        session.lease_expires_at = None
        self.touch(session, durable=True)

    def try_turn_lock(self, run_id: str) -> bool:
        return self._repo.try_turn_lock(run_id)

    def release_turn_lock(self, run_id: str) -> None:
        self._repo.release_turn_lock(run_id)

    def get_receipt(self, key: str) -> dict[str, Any] | None:
        return self._repo.get_receipt(key)

    def save_receipt(
        self,
        key: str,
        run_id: str,
        endpoint: str,
        response: dict[str, Any],
    ) -> None:
        self._repo.save_receipt(
            key, run_id, endpoint, response, _now()
        )

    def touch(self, session: Session, *, durable: bool = False) -> None:
        """Update the in-memory working set; persist only when durable/flushed."""
        session.updated_at = _now()
        self._sessions[session.id] = session
        if durable:
            self._persist(session)
            self._dirty.discard(session.id)
        else:
            self._dirty.add(session.id)

    def flush(self, session: Session | None = None) -> None:
        if session is not None:
            if session.id in self._dirty or session.id in self._sessions:
                self._sessions[session.id] = session
                if session.id in self._dirty:
                    self._persist(session)
                    self._dirty.discard(session.id)
            return
        for session_id in list(self._dirty):
            current = self._sessions.get(session_id)
            if current is None:
                self._dirty.discard(session_id)
                continue
            self._persist(current)
            self._dirty.discard(session_id)

    def _sorted(self) -> list[Session]:
        return sorted(self._sessions.values(), key=lambda s: s.updated_at, reverse=True)

    def list_ids(self) -> list[str]:
        return [s.id for s in self._sorted()]

    def count(self) -> int:
        return len(self._sessions)

    def list_summaries(self, limit: int = 50) -> list[dict[str, Any]]:
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
            for s in self._sorted()[: max(1, limit)]
        ]


# Import-safe default; FastAPI lifespan binds the configured PostgreSQL URL.
store = SessionStore("memory")
