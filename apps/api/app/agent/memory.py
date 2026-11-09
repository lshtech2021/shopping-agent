"""Generic memory contract used by the runtime.

Domain adapters decide what a run snapshot contains and how queries are
resolved. The runtime only calls these four methods.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Memory(Protocol):
    async def load(self, run_id: str) -> dict[str, Any]: ...

    async def save(self, run_id: str, patch: dict[str, Any]) -> None: ...

    async def query(
        self, query: str, *, limit: int
    ) -> list[dict[str, Any]]: ...

    async def summarize(self, run_id: str) -> str: ...
