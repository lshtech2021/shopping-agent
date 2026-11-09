"""Generic planning contracts; business plan content lives in metadata."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from app.agent.state import Plan


@runtime_checkable
class Planner(Protocol):
    async def plan(
        self, *, goal: str, context: dict[str, Any]
    ) -> Plan: ...
