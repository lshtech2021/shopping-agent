"""Extension WebSocket hub: ping/pong and browser job dispatch.

Single-user assumption: jobs go to the first connected extension. A dropped
connection fails its in-flight jobs immediately instead of letting them hang
until the timeout.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Optional

from fastapi import WebSocket

from app.config import settings
from app.store import Session
from app.trace import log_event

logger = logging.getLogger(__name__)


def origin_allowed(
    origin: str | None, extension_id: str | None = None
) -> bool:
    """Require the configured extension ID when one is configured."""
    if not origin:
        return True
    if not origin.startswith("chrome-extension://"):
        return False
    if not extension_id:
        return True
    return origin == f"chrome-extension://{extension_id}"


class ExtensionHub:
    def __init__(self) -> None:
        self._connections: dict[str, WebSocket] = {}
        self._pending: dict[str, asyncio.Future] = {}
        self._pending_conn: dict[str, str] = {}
        self._last_seen: dict[str, float] = {}
        self._disconnect_tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self.disconnect_grace = 5.0

    @property
    def online_count(self) -> int:
        return len(self._connections)

    async def connect(self, websocket: WebSocket) -> str:
        await websocket.accept()
        conn_id = uuid.uuid4().hex
        async with self._lock:
            self._connections[conn_id] = websocket
            self._last_seen[conn_id] = time.monotonic()
        logger.info("extension connected %s (online=%s)", conn_id[:8], self.online_count)
        return conn_id

    async def disconnect(self, conn_id: str) -> None:
        async with self._lock:
            self._connections.pop(conn_id, None)
            self._last_seen.pop(conn_id, None)
        pending = [
            job_id
            for job_id, owner in list(self._pending_conn.items())
            if owner == conn_id
        ]
        for job_id in pending:
            task = self._disconnect_tasks.pop(job_id, None)
            if task is not None:
                task.cancel()
            self._disconnect_tasks[job_id] = asyncio.create_task(
                self._fail_after_reconnect_grace(
                    job_id, conn_id, self.disconnect_grace
                )
            )
        logger.info(
            "extension disconnected %s (online=%s, pending_jobs=%s)",
            conn_id[:8],
            self.online_count,
            len(pending),
        )

    async def _fail_after_reconnect_grace(
        self, job_id: str, conn_id: str, grace: float = 1.5
    ) -> None:
        try:
            await asyncio.sleep(grace)
        except asyncio.CancelledError:
            return
        self._disconnect_tasks.pop(job_id, None)
        if self._pending_conn.get(job_id) != conn_id:
            return
        fut = self._pending.get(job_id)
        if fut and not fut.done():
            fut.set_result(
                {"status": "blocked", "error": "extension_disconnected"}
            )

    async def handle_message(self, conn_id: str, message: dict[str, Any]) -> Optional[dict]:
        msg_type = message.get("type")
        self._last_seen[conn_id] = time.monotonic()
        if msg_type == "ping":
            return {"type": "pong", "ts": message.get("ts")}
        if msg_type == "job_result":
            job_id = str(message.get("job_id") or "")
            owner = self._pending_conn.get(job_id)
            if owner is None:
                return {"type": "error", "error": "unknown_job", "job_id": job_id}
            if owner != conn_id and owner in self._connections:
                logger.warning(
                    "job_result owner mismatch job=%s expected=%s got=%s",
                    job_id[:8],
                    owner[:8],
                    conn_id[:8],
                )
                return {
                    "type": "error",
                    "error": "job_owner_mismatch",
                    "job_id": job_id,
                }
            if owner != conn_id:
                logger.info(
                    "accepting redelivered job_result job=%s from %s",
                    job_id[:8],
                    conn_id[:8],
                )
                task = self._disconnect_tasks.pop(job_id, None)
                if task is not None:
                    task.cancel()
                self._pending_conn[job_id] = conn_id
            fut = self._pending.get(job_id)
            result = message.get("result") or {}
            logger.info(
                "job_result job_id=%s status=%s logs=%s",
                job_id[:8],
                result.get("status"),
                len(result.get("extension_logs") or []),
            )
            if fut and not fut.done():
                fut.set_result(result)
            return {"type": "ack", "job_id": job_id}
        return {"type": "error", "detail": f"unknown type: {msg_type}"}

    async def dispatch_job(
        self,
        tool_name: str,
        args: dict[str, Any],
        timeout: float = 60.0,
        session: Session | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            if not self._connections:
                if session is not None:
                    log_event(
                        session,
                        "无在线 Extension，无法派发浏览任务",
                        stage="ws.dispatch",
                        source="ws",
                        level="warning",
                        data={"tool": tool_name, "args": args},
                    )
                return {"status": "blocked", "error": "no_extension_online"}
            conn_id = max(
                self._connections,
                key=lambda cid: self._last_seen.get(cid, 0.0),
            )
            websocket = self._connections[conn_id]

        job_id = uuid.uuid4().hex
        from app.browser.action_broker import ActionBroker

        broker = ActionBroker(settings.database_url or "memory")
        action = await broker.create_action(
            session.id if session is not None else "anonymous",
            tool_name,
            args,
            turn=session.turn if session is not None else None,
        )
        if action.status == "done" and action.result is not None:
            return action.result
        action_id = action.action_id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[job_id] = fut
        self._pending_conn[job_id] = conn_id
        started = time.perf_counter()
        logger.info("dispatch job=%s tool=%s conn=%s", job_id[:8], tool_name, conn_id[:8])
        if session is not None:
            log_event(
                session,
                f"派发 Extension 任务 {tool_name}",
                stage="ws.dispatch",
                source="ws",
                level="debug",
                data={"job_id": job_id, "tool": tool_name, "args": args, "conn_id": conn_id},
            )
        try:
            try:
                await websocket.send_json(
                    {
                        "type": "job",
                        "job_id": job_id,
                        "action_id": action_id,
                        "run_id": session.id if session is not None else None,
                        "tool": tool_name,
                        "args": args,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                async with self._lock:
                    self._connections.pop(conn_id, None)
                    self._last_seen.pop(conn_id, None)
                return {
                    "status": "blocked",
                    "error": f"extension_send_failed: {exc}",
                }
            result = await asyncio.wait_for(fut, timeout=timeout)
            if not isinstance(result, dict):
                return {"status": "parse_error", "error": "invalid_result"}
            if result.get("status") == "ok":
                try:
                    await broker.submit_observation(action_id, result)
                except Exception:  # noqa: BLE001 — result still returns
                    logger.warning("failed to persist observation %s", action_id[:8])
            if session is not None:
                items = result.get("items")
                log_event(
                    session,
                    f"Extension 任务返回 {tool_name}={result.get('status')}",
                    stage="ws.result",
                    source="ws",
                    level="warning" if result.get("error") else "info",
                    duration_ms=(time.perf_counter() - started) * 1000,
                    data={
                        "job_id": job_id,
                        "status": result.get("status"),
                        "error": result.get("error"),
                        "item_count": len(items) if isinstance(items, list) else None,
                    },
                )
            return result
        except asyncio.TimeoutError:
            if session is not None:
                log_event(
                    session,
                    f"Extension 任务超时 {tool_name}",
                    stage="ws.timeout",
                    source="ws",
                    level="error",
                    duration_ms=(time.perf_counter() - started) * 1000,
                    data={"job_id": job_id, "timeout": timeout},
                )
            return {"status": "blocked", "error": "job_timeout", "job_id": job_id}
        except Exception as exc:  # noqa: BLE001
            logger.warning("dispatch_job failed: %s", exc)
            if session is not None:
                log_event(
                    session,
                    f"Extension 任务失败 {tool_name}",
                    stage="ws.error",
                    source="ws",
                    level="error",
                    data={"error": str(exc), "job_id": job_id},
                )
            return {"status": "blocked", "error": str(exc)}
        finally:
            self._pending.pop(job_id, None)
            self._pending_conn.pop(job_id, None)


hub = ExtensionHub()
