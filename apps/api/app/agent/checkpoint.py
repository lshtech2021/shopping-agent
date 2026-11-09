"""Durable run-state checkpoints for interrupt/resume."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from langchain_core.messages import messages_from_dict, messages_to_dict

from app.store_pg import RunRepository


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def serialize_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": state.get("run_id"),
        "messages": messages_to_dict(state.get("messages") or []),
        "plan": copy.deepcopy(state.get("plan")),
        "step": int(state.get("step") or 0),
        "turn": int(state.get("turn") or 0),
        "phase": state.get("phase"),
        "tool_results": copy.deepcopy(state.get("tool_results") or []),
        "pending_user": copy.deepcopy(state.get("pending_user")),
        "error": state.get("error"),
    }


def deserialize_state(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": data.get("run_id") or "",
        "messages": messages_from_dict(data.get("messages") or []),
        "plan": copy.deepcopy(data.get("plan")),
        "step": int(data.get("step") or 0),
        "turn": int(data.get("turn") or 0),
        "phase": data.get("phase") or "executing",
        "tool_results": copy.deepcopy(data.get("tool_results") or []),
        "pending_user": copy.deepcopy(data.get("pending_user")),
        "error": data.get("error"),
    }


@runtime_checkable
class CheckpointStore(Protocol):
    async def save(self, run_id: str, state: dict[str, Any]) -> None: ...

    async def load(self, run_id: str) -> dict[str, Any] | None: ...

    async def delete(self, run_id: str) -> None: ...


class MemoryCheckpointStore:
    def __init__(self) -> None:
        self._states: dict[str, dict[str, Any]] = {}

    async def save(self, run_id: str, state: dict[str, Any]) -> None:
        self._states[run_id] = serialize_state(state)

    async def load(self, run_id: str) -> dict[str, Any] | None:
        state = self._states.get(run_id)
        return deserialize_state(copy.deepcopy(state)) if state else None

    async def delete(self, run_id: str) -> None:
        self._states.pop(run_id, None)


class PgCheckpointStore:
    def __init__(self, database_url: str = "memory") -> None:
        self.repo = RunRepository(database_url)

    async def save(self, run_id: str, state: dict[str, Any]) -> None:
        self.repo.save_checkpoint(run_id, serialize_state(state), _now())

    async def load(self, run_id: str) -> dict[str, Any] | None:
        state = self.repo.load_checkpoint(run_id)
        return deserialize_state(state) if state else None

    async def delete(self, run_id: str) -> None:
        self.repo.delete_checkpoint(run_id)
