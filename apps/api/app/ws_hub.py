"""Extension WebSocket hub: ping/pong and browser job dispatch."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Optional

from fastapi import WebSocket

from app.store import Session
from app.trace import log_event

logger = logging.getLogger(__name__)


class ExtensionHub:
    def __init__(self) -> None:
        self._connections: dict[str, WebSocket] = {}
        self._pending: dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()

    @property
    def online_count(self) -> int:
        return len(self._connections)

    async def connect(self, websocket: WebSocket) -> str:
        await websocket.accept()
        conn_id = uuid.uuid4().hex
        async with self._lock:
            self._connections[conn_id] = websocket
        logger.info("extension connected %s (online=%s)", conn_id, self.online_count)
        return conn_id

    async def disconnect(self, conn_id: str) -> None:
        async with self._lock:
            self._connections.pop(conn_id, None)
        logger.info("extension disconnected %s (online=%s)", conn_id, self.online_count)

    async def handle_message(self, conn_id: str, message: dict[str, Any]) -> Optional[dict]:
        msg_type = message.get("type")
        if msg_type == "ping":
            return {"type": "pong", "ts": message.get("ts")}
        if msg_type == "job_result":
            job_id = str(message.get("job_id") or "")
            fut = self._pending.get(job_id)
            result = message.get("result") or {}
            logger.info(
                "job_result job_id=%s status=%s logs=%s",
                job_id[:8],
                (result or {}).get("status"),
                len((result or {}).get("extension_logs") or []),
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
                return {
                    "status": "blocked",
                    "error": "no_extension_online",
                }
            conn_id, websocket = next(iter(self._connections.items()))

        job_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[job_id] = fut
        payload = {
            "type": "job",
            "job_id": job_id,
            "tool": tool_name,
            "args": args,
        }
        started = time.perf_counter()
        logger.info(
            "dispatch job=%s tool=%s conn=%s args=%s",
            job_id[:8],
            tool_name,
            conn_id[:8],
            args,
        )
        if session is not None:
            log_event(
                session,
                f"派发 Extension 任务 {tool_name}",
                stage="ws.dispatch",
                source="ws",
                level="debug",
                data={"job_id": job_id, "tool": tool_name, "args": args, "conn_id": conn_id},
                persist=False,
            )
        try:
            await websocket.send_json(payload)
            result = await asyncio.wait_for(fut, timeout=timeout)
            if not isinstance(result, dict):
                return {"status": "parse_error", "error": "invalid_result"}
            if session is not None:
                log_event(
                    session,
                    f"Extension 任务返回 {tool_name}={result.get('status')}",
                    stage="ws.result",
                    source="ws",
                    duration_ms=(time.perf_counter() - started) * 1000,
                    data={
                        "job_id": job_id,
                        "status": result.get("status"),
                        "error": result.get("error"),
                        "item_count": len(result.get("items") or [])
                        if isinstance(result.get("items"), list)
                        else None,
                    },
                    persist=False,
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


hub = ExtensionHub()
