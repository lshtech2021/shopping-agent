"""Run leases, crash reconciliation, and expired-lease sweeps."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.store import Session, SessionStore, store as default_store
from app.store_pg import VersionConflict


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RunSupervisor:
    def __init__(self, store: SessionStore | None = None) -> None:
        self.store = store or default_store

    def _safe_touch(self, session: Session) -> bool:
        try:
            self.store.touch(session)
            return True
        except VersionConflict:
            return False

    def reconcile_startup(self) -> int:
        changed = 0
        for session_id in self.store.list_ids():
            session = self.store.get(session_id)
            if session is None or session.status != "running":
                continue
            if (
                session.lease_owner
                and session.lease_expires_at
                and session.lease_expires_at > _now().isoformat()
            ):
                continue
            session.status = "interrupted"
            session.interrupt_reason = "server_restart"
            session.lease_owner = None
            session.lease_expires_at = None
            session.heartbeat_at = None
            if self._safe_touch(session):
                changed += 1
        return changed

    def sweep_expired(self) -> int:
        now = _now().isoformat()
        changed = 0
        for session_id in self.store.list_ids():
            session = self.store.get(session_id)
            if session is None or not session.lease_expires_at:
                continue
            if session.lease_expires_at >= now:
                continue
            if session.status == "running":
                session.status = "interrupted"
                session.interrupt_reason = "lease_expired"
            session.lease_owner = None
            session.lease_expires_at = None
            session.heartbeat_at = None
            if self._safe_touch(session):
                changed += 1
        return changed

    def acquire(self, run_id: str, ttl_seconds: int = 120) -> str | None:
        session = self.store.refresh(run_id)
        if session is None:
            return None
        now = _now()
        if (
            session.lease_owner
            and session.lease_expires_at
            and session.lease_expires_at > now.isoformat()
        ):
            return None
        owner = uuid.uuid4().hex
        expires = (now + timedelta(seconds=ttl_seconds)).isoformat()
        if self.store.persistent:
            if not self.store.acquire_lease(run_id, owner, expires):
                return None
            session = self.store.refresh(run_id)
            return owner if session and session.lease_owner == owner else None
        session.lease_owner = owner
        session.lease_expires_at = expires
        session.heartbeat_at = now.isoformat()
        return owner if self._safe_touch(session) else None

    def heartbeat(self, run_id: str, owner: str, ttl_seconds: int = 120) -> bool:
        session = self.store.get(run_id)
        if session is None or session.lease_owner != owner:
            return False
        session.heartbeat_at = _now().isoformat()
        session.lease_expires_at = (
            _now() + timedelta(seconds=ttl_seconds)
        ).isoformat()
        return self._safe_touch(session)

    def release(self, run_id: str, owner: str) -> None:
        session = self.store.get(run_id)
        if session is None or session.lease_owner != owner:
            return
        session.lease_owner = None
        session.lease_expires_at = None
        self._safe_touch(session)
