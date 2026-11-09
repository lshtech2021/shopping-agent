"""Session execution trace helpers for optimization review.

Events live on ``session.execution_logs`` (in-memory immediately, written to
PostgreSQL at the next ``store.touch``). Each event carries the session ``turn``
so clients can fetch only the logs of the turn that just ran.
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from app.store import Session, store

logger = logging.getLogger("shopping_agent.trace")

MAX_LOGS = 400
MAX_DATA_CHARS = 1200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trim(value: Any, limit: int = MAX_DATA_CHARS) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        text = str(value)
        return value if not isinstance(value, str) or len(text) <= limit else text[: limit - 3] + "..."
    if isinstance(value, dict):
        return {str(k): _trim(v, limit // 2) for k, v in list(value.items())[:40]}
    if isinstance(value, (list, tuple)):
        return [_trim(v, limit // 2) for v in list(value)[:30]]
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def log_event(
    session: Session,
    message: str,
    *,
    stage: str,
    source: str = "api",
    level: str = "info",
    duration_ms: Optional[float] = None,
    data: Optional[dict[str, Any]] = None,
    persist: bool = False,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "ts": _now(),
        "turn": session.turn,
        "level": level,
        "source": source,
        "stage": stage,
        "message": message,
    }
    if duration_ms is not None:
        event["duration_ms"] = round(float(duration_ms), 1)
    if data:
        event["data"] = _trim(data)

    session.execution_logs.append(event)
    if len(session.execution_logs) > MAX_LOGS:
        session.execution_logs = session.execution_logs[-MAX_LOGS:]

    line = f"sid={session.id[:8]} turn={session.turn} stage={stage} {message}"
    if duration_ms is not None:
        line += f" ({duration_ms:.0f}ms)"
    log_fn = getattr(logger, level if level in {"debug", "info", "warning", "error"} else "info")
    log_fn("%s | %s", line, _trim(data) if data else "")

    if persist:
        store.touch(session)
    return event


def turn_logs(session: Session, limit: int = 200) -> list[dict[str, Any]]:
    """Events emitted during the session's current turn."""
    return [e for e in session.execution_logs if e.get("turn") == session.turn][-limit:]


@contextmanager
def timed_step(
    session: Session,
    message: str,
    *,
    stage: str,
    source: str = "agent",
    data: Optional[dict[str, Any]] = None,
) -> Iterator[dict[str, Any]]:
    started = time.perf_counter()
    log_event(session, f"开始：{message}", stage=stage, source=source, level="debug", data=data)
    meta: dict[str, Any] = {"ok": True}
    try:
        yield meta
    except Exception as exc:  # noqa: BLE001
        meta["ok"] = False
        meta["error"] = str(exc)
        log_event(
            session,
            f"失败：{message}: {exc}",
            stage=stage,
            source=source,
            level="error",
            duration_ms=(time.perf_counter() - started) * 1000,
            data={**(data or {}), **meta},
        )
        raise
    else:
        suffix = meta.get("summary") or "完成"
        log_event(
            session,
            f"{suffix}：{message}",
            stage=stage,
            source=source,
            level="warning" if meta.get("warning") else "info",
            duration_ms=(time.perf_counter() - started) * 1000,
            data={**(data or {}), **{k: v for k, v in meta.items() if k != "summary"}},
        )
