"""Idempotent browser action broker backed by durable action rows."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from app.store_pg import RunRepository


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Action(BaseModel):
    action_id: str
    run_id: str
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    status: str = "pending"
    result: dict[str, Any] | None = None


class ActionBroker:
    def __init__(self, database_url: str = "memory") -> None:
        self.repo = RunRepository(database_url)

    @staticmethod
    def deterministic_id(
        run_id: str,
        tool: str,
        args: dict[str, Any],
        *,
        turn: int | None = None,
    ) -> str:
        payload = json.dumps(
            {"run_id": run_id, "turn": turn, "tool": tool, "args": args},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def create_action(
        self,
        run_id: str,
        tool: str,
        args: dict[str, Any] | None = None,
        *,
        turn: int | None = None,
    ) -> Action:
        args = dict(args or {})
        action_id = self.deterministic_id(
            run_id, tool, args, turn=turn
        )
        now = _now()
        record = self.repo.create_action(
            {
                "action_id": action_id,
                "run_id": run_id,
                "tool": tool,
                "args": args,
                "status": "pending",
                "result": None,
                "created_at": now,
                "updated_at": now,
            }
        )
        return self._to_action(record)

    async def next_action(self, run_id: str) -> Action | None:
        record = self.repo.next_action(run_id)
        return self._to_action(record) if record else None

    async def submit_observation(
        self, action_id: str, observation: dict[str, Any]
    ) -> dict[str, Any]:
        action = self.repo.get_action(action_id)
        if action is None:
            raise KeyError(action_id)
        return self.repo.submit_observation(
            {
                "action_id": action_id,
                "run_id": action["run_id"],
                "payload": dict(observation or {}),
                "created_at": _now(),
            }
        )

    async def get_action(self, action_id: str) -> Action | None:
        record = self.repo.get_action(action_id)
        return self._to_action(record) if record else None

    @staticmethod
    def _to_action(record: dict[str, Any]) -> Action:
        return Action(
            action_id=str(record["action_id"]),
            run_id=str(record["run_id"]),
            tool=str(record["tool"]),
            args=dict(record.get("args") or {}),
            status=str(record.get("status") or "pending"),
            result=record.get("result"),
        )
