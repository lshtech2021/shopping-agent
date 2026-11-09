"""Generic agent state shared by every domain."""
from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


class AgentPhase(str, Enum):
    PLANNING = "planning"
    EXECUTING = "executing"
    WAITING_USER = "waiting_user"
    DONE = "done"
    FAILED = "failed"


class PlanStep(BaseModel):
    id: str
    description: str
    status: Literal["pending", "in_progress", "done", "skipped"] = "pending"
    evidence: list[str] = Field(default_factory=list)


class Plan(BaseModel):
    goal: str
    steps: list[PlanStep]
    metadata: dict[str, Any] = Field(default_factory=dict)


class PendingUser(BaseModel):
    question: str
    reason: str
    options: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentState(TypedDict):
    run_id: str
    messages: Annotated[list[AnyMessage], add_messages]
    plan: dict | None
    step: int
    phase: str
    tool_results: list[dict]
    pending_user: dict | None
    error: str | None
