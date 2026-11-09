from __future__ import annotations

import asyncio
import uuid

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel

from app.agent.checkpoint import MemoryCheckpointStore
from app.agent.runtime import AgentRuntime
from app.agent.state import AgentState
from app.agent.tools import ToolRegistry, ToolResult, ToolSpec


class EmptyArgs(BaseModel):
    pass


def _state() -> AgentState:
    return {
        "run_id": "run-checkpoint",
        "messages": [HumanMessage(content="find a laptop")],
        "plan": None,
        "step": 0,
        "phase": "executing",
        "tool_results": [],
        "pending_user": None,
        "error": None,
    }


class CrashOncePolicy:
    def __init__(self, tool_call):
        self.calls = 0
        self.tool_call = tool_call

    async def next_action(self, state, context):
        self.calls += 1
        if self.calls == 1:
            return self.tool_call
        raise RuntimeError("worker crashed")


class TextPolicy:
    def __init__(self, text="resumed"):
        self.calls = 0
        self.text = text

    async def next_action(self, state, context):
        self.calls += 1
        return AIMessage(content=self.text)


class Builder:
    def build(self, **_kwargs):
        return [SystemMessage(content="ctx")]


def _runtime(store, handler_calls):
    async def handler(_args, _context):
        handler_calls.append(1)
        return ToolResult(call_id="", tool="tool_a", ok=True)

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="tool_a",
            description="tool_a",
            args_model=EmptyArgs,
            handler=handler,
        )
    )
    return AgentRuntime(
        tools=registry,
        context_builder=Builder(),
        max_steps=4,
        checkpoint_store=store,
    )


def test_checkpoint_resumes_after_worker_crash_without_duplicate_tool_call():
    store = MemoryCheckpointStore()
    handler_calls: list[int] = []
    runtime = _runtime(store, handler_calls)
    ai = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "tool_a",
                "args": {},
                "id": uuid.uuid4().hex,
                "type": "tool_call",
            }
        ],
    )
    state = _state()

    try:
        asyncio.run(
            runtime.run_turn(state=state, policy=CrashOncePolicy(ai))
        )
    except RuntimeError as exc:
        assert "worker crashed" in str(exc)

    saved = asyncio.run(store.load("run-checkpoint"))
    assert saved is not None
    assert any(isinstance(message, ToolMessage) for message in saved["messages"])
    assert len(handler_calls) == 1

    resumed_runtime = _runtime(store, handler_calls)
    result = asyncio.run(
        resumed_runtime.resume_from_checkpoint(
            run_id="run-checkpoint", policy=TextPolicy("resumed")
        )
    )

    assert result is not None
    assert result.status == "done"
    assert result.output == {"text": "resumed"}
    assert len(handler_calls) == 1


def test_checkpoint_resume_can_append_user_input_after_wait():
    store = MemoryCheckpointStore()
    calls: list[int] = []

    async def wait_handler(_args, _context):
        return ToolResult(
            call_id="",
            tool="ask",
            ok=True,
            control="wait_user",
            output={"question": "budget?", "reason": "clarify"},
        )

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="ask",
            description="ask",
            args_model=EmptyArgs,
            handler=wait_handler,
        )
    )
    runtime = AgentRuntime(
        tools=registry,
        context_builder=Builder(),
        checkpoint_store=store,
    )
    ai = AIMessage(
        content="",
        tool_calls=[
            {"name": "ask", "args": {}, "id": "call-1", "type": "tool_call"}
        ],
    )
    state2 = _state()
    state2["run_id"] = "run-wait"
    from app.agent.policies.react import ReActPolicy

    class Model:
        def __init__(self):
            self.calls = 0

        async def ainvoke(self, _messages):
            self.calls += 1
            return ai

    waiting = asyncio.run(
        runtime.run_turn(
            state=state2, policy=ReActPolicy(model=Model())
        )
    )
    assert waiting.status == "waiting_user"

    saved = asyncio.run(store.load("run-wait"))
    assert saved is not None
    assert saved["pending_user"]["question"] == "budget?"

    resumed = asyncio.run(
        runtime.resume_from_checkpoint(
            run_id="run-wait",
            policy=TextPolicy("after answer"),
            user_input="5000",
        )
    )
    assert resumed.status == "done"
