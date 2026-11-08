"""Extension WebSocket hub: ping/pong and browser job dispatch."""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Optional

from fastapi import WebSocket

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
            if fut and not fut.done():
                fut.set_result(message.get("result") or {})
            return {"type": "ack", "job_id": job_id}
        return {"type": "error", "detail": f"unknown type: {msg_type}"}

    async def dispatch_job(
        self,
        tool_name: str,
        args: dict[str, Any],
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        async with self._lock:
            if not self._connections:
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
        try:
            await websocket.send_json(payload)
            result = await asyncio.wait_for(fut, timeout=timeout)
            if not isinstance(result, dict):
                return {"status": "parse_error", "error": "invalid_result"}
            return result
        except asyncio.TimeoutError:
            return {"status": "blocked", "error": "job_timeout", "job_id": job_id}
        except Exception as exc:  # noqa: BLE001
            logger.warning("dispatch_job failed: %s", exc)
            return {"status": "blocked", "error": str(exc)}
        finally:
            self._pending.pop(job_id, None)


hub = ExtensionHub()
