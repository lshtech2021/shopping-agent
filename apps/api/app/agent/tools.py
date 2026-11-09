"""Typed tool contracts for the generic runtime."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Literal, Protocol

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from app.agent.memory import Memory


class ToolResult(BaseModel):
    call_id: str
    tool: str
    ok: bool
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    untrusted: bool = True
    meta: dict[str, Any] = Field(default_factory=dict)
    # Generic control signals; the runtime never inspects business tool names.
    control: Literal["continue", "finish", "wait_user", "fail"] = "continue"
    output: dict[str, Any] | None = None


class BrowserClient(Protocol):
    async def execute(self, action: dict[str, Any]) -> dict[str, Any]: ...


class Tracer(Protocol):
    def event(self, name: str, **fields: Any) -> None: ...


@dataclass
class ToolContext:
    run_id: str
    memory: "Memory"
    browser: BrowserClient
    trace: Tracer
    resources: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[[BaseModel, ToolContext], Awaitable[ToolResult]]
    read_only: bool = False
    idempotent: bool = False


class ToolRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"tool already registered: {spec.name}")
        self._specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def specs(self) -> list[ToolSpec]:
        return list(self._specs.values())
