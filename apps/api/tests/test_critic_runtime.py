from __future__ import annotations

import asyncio
import uuid

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from app.agent.critic import Critique
from app.agent.runtime import AgentRuntime
from app.agent.state import AgentState
from app.agent.tools import ToolRegistry, ToolResult, ToolSpec


class EmptyArgs(BaseModel):
    pass


def _state() -> AgentState:
    return {
        "run_id": "run-1",
        "messages": [],
        "plan": None,
        "step": 0,
        "phase": "executing",
        "tool_results": [],
        "pending_user": None,
        "error": None,
    }


class Model:
    def __init__(self):
        self.calls = 0

    async def ainvoke(self, _messages):
        self.calls += 1
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "finish",
                    "args": {},
                    "id": uuid.uuid4().hex,
                    "type": "tool_call",
                }
            ],
        )


def _runtime(critic, finalize, max_iterations=2):
    attempts = {"value": 0}

    async def finish(_args, _context):
        attempts["value"] += 1
        return ToolResult(
            call_id="",
            tool="finish",
            ok=True,
            control="finish",
            output={"reply": f"attempt-{attempts['value']}"},
        )

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="finish",
            description="finish",
            args_model=EmptyArgs,
            handler=finish,
        )
    )

    class Builder:
        def build(self, **_kwargs):
            return [SystemMessage(content="ctx")]

    runtime = AgentRuntime(
        tools=registry,
        context_builder=Builder(),
        max_steps=5,
        critic=critic,
        max_critic_iterations=max_iterations,
        finalize=finalize,
    )
    return runtime, Model(), attempts


def test_critic_rejection_retries_before_finish_is_accepted():
    critiques = [
        Critique(ok=False, violations=["budget"], retry=True, hints=["drop it"]),
        Critique(ok=True),
    ]
    finalized = []

    class Critic:
        async def evaluate(self, *, state, output):
            return critiques.pop(0)

    async def finalize(state, output):
        finalized.append(output["reply"])

    runtime, model, attempts = _runtime(Critic(), finalize)
    state = _state()
    result = asyncio.run(
        runtime.run_turn(state=state, policy=_policy(model))
    )

    assert result.status == "done"
    assert result.output["reply"] == "attempt-2"
    assert model.calls == 2
    assert attempts["value"] == 2
    assert finalized == ["attempt-2"]
    assert any(
        isinstance(message, HumanMessage)
        and message.content.startswith("[CRITIC_FEEDBACK]")
        for message in state["messages"]
    )


def test_critic_rejection_after_max_iterations_fails_without_finalize():
    finalized = []

    class Critic:
        async def evaluate(self, *, state, output):
            return Critique(ok=False, retry=True, violations=["budget"])

    async def finalize(state, output):
        finalized.append(output)

    runtime, model, _attempts = _runtime(
        Critic(), finalize, max_iterations=1
    )
    result = asyncio.run(
        runtime.run_turn(state=_state(), policy=_policy(model))
    )

    assert result.status == "failed"
    assert result.error == "critic_rejected"
    assert model.calls == 2
    assert finalized == []


def test_finalize_failure_returns_failed_without_accepting_output():
    class Critic:
        async def evaluate(self, *, state, output):
            return Critique(ok=True)

    async def finalize(state, output):
        raise RuntimeError("commit failed")

    runtime, model, _attempts = _runtime(Critic(), finalize)
    result = asyncio.run(
        runtime.run_turn(state=_state(), policy=_policy(model))
    )

    assert result.status == "failed"
    assert result.error.startswith("finalize_failed:")
    assert model.calls == 1


def test_critic_error_is_advisory_and_does_not_kill_the_turn():
    finalized = []

    class Critic:
        async def evaluate(self, *, state, output):
            raise RuntimeError("critic down")

    async def finalize(state, output):
        finalized.append(output["reply"])

    runtime, model, _attempts = _runtime(Critic(), finalize)
    result = asyncio.run(
        runtime.run_turn(state=_state(), policy=_policy(model))
    )

    assert result.status == "done"
    assert finalized == ["attempt-1"]


def _policy(model):
    from app.agent.policies.react import ReActPolicy

    return ReActPolicy(model=model)
