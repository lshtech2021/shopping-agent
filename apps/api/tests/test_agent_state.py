from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest
from pydantic import BaseModel

import app.agent
from app.agent.critic import Critique
from app.agent.state import AgentPhase, AgentState, Plan, PlanStep
from app.agent.tools import ToolContext, ToolRegistry, ToolSpec, ToolResult


class EmptyArgs(BaseModel):
    pass


async def _handler(_args: BaseModel, _context: ToolContext) -> ToolResult:
    return ToolResult(call_id="call-1", tool="echo", ok=True)


def _spec(name: str = "echo") -> ToolSpec:
    return ToolSpec(
        name=name,
        description="echo",
        args_model=EmptyArgs,
        handler=_handler,
    )


def test_plan_round_trip_and_no_business_fields():
    plan = Plan(
        goal="pick a product",
        steps=[
            PlanStep(
                id="search",
                description="search inventory",
                status="done",
                evidence=["search returned 10 items"],
            )
        ],
        metadata={"query": "laptop"},
    )

    assert Plan.model_validate(plan.model_dump()) == plan
    assert "search_queries" not in Plan.model_fields
    assert "evidence_checklist" not in Plan.model_fields


def test_tool_result_round_trip_with_control_signal():
    result = ToolResult(
        call_id="call-1",
        tool="finish",
        ok=True,
        data={"items": [1, 2]},
        control="finish",
        output={"reply": "done"},
    )

    assert ToolResult.model_validate(result.model_dump()) == result
    assert result.untrusted is True
    assert result.control == "finish"


def test_pending_user_and_phase_are_generic():
    from app.agent.state import PendingUser

    pending = PendingUser(question="Which one?", reason="clarify")
    assert pending.options == []
    assert AgentPhase.WAITING_USER.value == "waiting_user"
    assert "requirements" not in AgentState.__annotations__
    assert "products" not in AgentState.__annotations__


def test_tool_registry_rejects_duplicates_and_unknown_returns_none():
    registry = ToolRegistry()
    spec = _spec()
    registry.register(spec)

    assert registry.get("echo") is spec
    assert registry.get("missing") is None
    assert registry.specs() == [spec]

    with pytest.raises(ValueError):
        registry.register(_spec())


def test_tool_context_has_no_session_or_toolbox():
    fields = {field.name for field in dataclasses.fields(ToolContext)}
    assert fields == {"run_id", "memory", "browser", "trace", "resources"}
    assert "session" not in fields
    assert "toolbox" not in fields


def test_critique_is_generic():
    critique = Critique(ok=False, violations=["budget"], hints=["reduce price"])
    assert critique.retry is False
    assert critique.scores == {}


def test_agent_package_does_not_import_domains_or_shopping():
    package_dir = Path(app.agent.__file__).resolve().parent
    imported: list[str] = []
    for path in package_dir.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
            elif isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)

    assert imported
    assert not any(
        module == "shopping"
        or module.startswith("shopping.")
        or module == "app.domains"
        or module.startswith("app.domains.")
        for module in imported
    )
