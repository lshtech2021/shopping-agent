"""In-memory session store for early phases (SQLite later)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


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
    interrupt_reason: Optional[str] = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

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
        return session

    def get(self, session_id: str) -> Optional[Session]:
        return self._sessions.get(session_id)

    def touch(self, session: Session) -> None:
        session.updated_at = _now()

    def list_ids(self) -> list[str]:
        return list(self._sessions.keys())


store = SessionStore()
