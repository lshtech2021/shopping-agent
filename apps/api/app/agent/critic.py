"""Generic critic contract; rubrics belong to domain packs."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from app.agent.state import AgentState


class Critique(BaseModel):
    ok: bool
    scores: dict[str, float] = Field(default_factory=dict)
    violations: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    retry: bool = False
    hints: list[str] = Field(default_factory=list)


class Critic(Protocol):
    async def evaluate(
        self, *, state: "AgentState", output: dict[str, Any]
    ) -> Critique: ...
