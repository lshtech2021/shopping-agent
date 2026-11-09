from __future__ import annotations

import asyncio
import json
import uuid

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel

from app.agent.context import ContextBuilder
from app.agent.runtime import AgentRuntime, TurnResult
from app.agent.state import AgentState, PendingUser
from app.agent.tools import ToolContext, ToolRegistry, ToolResult, ToolSpec


class EmptyArgs(BaseModel):
    pass


class FakeModel:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.seen_messages: list[list[BaseMessage]] = []

    def bind_tools(self, _tools):
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        self.seen_messages.append(list(messages))
        step = self.script.pop(0)
        return step(messages) if callable(step) else step


class RecordingBuilder:
    def __init__(self):
        self.calls = []

    def build(self, *, system_prompt, state, memory_summary, tool_specs):
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "state": state,
                "memory_summary": memory_summary,
                "tool_specs": [spec.name for spec in tool_specs],
            }
        )
        return [SystemMessage(content="CONTEXT")]


class Memory:
    async def summarize(self, run_id: str) -> str:
        return "MEMORY"


class Browser:
    async def execute(self, action):
        return action


class Trace:
    def event(self, name, **fields):
        return None


def call(name: str, **args) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": name,
                "args": args,
                "id": uuid.uuid4().hex,
                "type": "tool_call",
            }
        ],
    )


def spec(name, result):
    async def handler(_args, _context):
        return result

    return ToolSpec(
        name=name,
        description=name,
        args_model=EmptyArgs,
        handler=handler,
    )


def make_state(messages=None) -> AgentState:
    return {
        "run_id": "run-1",
        "messages": list(messages or []),
        "plan": None,
        "step": 0,
        "phase": "executing",
        "tool_results": [],
        "pending_user": None,
        "error": None,
    }


def runtime_for(tools, model, *, max_steps=8, critic=None):
    from app.agent.policies.react import ReActPolicy

    builder = RecordingBuilder()
    context = ToolContext(
        run_id="run-1",
        memory=Memory(),
        browser=Browser(),
        trace=Trace(),
    )
    runtime = AgentRuntime(
        tools=tools,
        context_builder=builder,
        critic=critic,
        max_steps=max_steps,
        system_prompt="SYSTEM",
        memory=Memory(),
        tool_context=context,
    )
    return runtime, ReActPolicy(model=model), builder


def test_runtime_runs_model_tools_model_and_returns_text():
    registry = ToolRegistry()
    registry.register(
        spec(
            "search",
            ToolResult(call_id="ignored", tool="search", ok=True, data={"items": [1]}),
        )
    )
    model = FakeModel([call("search", query="x"), AIMessage(content="final answer")])

    runtime, policy, builder = runtime_for(registry, model)
    state = make_state([HumanMessage(content="hello")])
    result = asyncio.run(runtime.run_turn(state=state, policy=policy))

    assert result.status == "done"
    assert result.output == {"text": "final answer"}
    assert result.steps == 2
    assert model.calls == 2
    assert builder.calls[0]["system_prompt"] == "SYSTEM"
    assert builder.calls[0]["memory_summary"] == "MEMORY"
    assert builder.calls[0]["tool_specs"] == ["search"]
    tool_messages = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert tool_messages and tool_messages[0].name == "search"
    assert "[UNTRUSTED_TOOL_RESULT]" in tool_messages[0].content
    assert state["tool_results"][0]["ok"] is True
    assert state["phase"] == "done"


def test_finish_control_stops_immediately():
    registry = ToolRegistry()
    registry.register(
        spec(
            "finish",
            ToolResult(
                call_id="ignored",
                tool="finish",
                ok=True,
                control="finish",
                output={"reply": "done"},
            ),
        )
    )
    model = FakeModel([call("finish")])

    runtime, policy, _ = runtime_for(registry, model)
    result = asyncio.run(runtime.run_turn(state=make_state(), policy=policy))

    assert result.status == "done"
    assert result.output == {"reply": "done"}
    assert model.calls == 1


def test_wait_user_control_returns_pending_user():
    registry = ToolRegistry()
    registry.register(
        spec(
            "ask_user",
            ToolResult(
                call_id="ignored",
                tool="ask_user",
                ok=True,
                control="wait_user",
                output={
                    "question": "Which budget?",
                    "reason": "need_user",
                    "options": ["3000", "5000"],
                },
            ),
        )
    )
    model = FakeModel([call("ask_user")])

    runtime, policy, _ = runtime_for(registry, model)
    state = make_state()
    result = asyncio.run(runtime.run_turn(state=state, policy=policy))

    assert result.status == "waiting_user"
    assert result.pending_user == PendingUser(
        question="Which budget?",
        reason="need_user",
        options=["3000", "5000"],
    )
    assert state["phase"] == "waiting_user"
    assert state["pending_user"]["question"] == "Which budget?"


def test_fail_control_returns_failed():
    registry = ToolRegistry()
    registry.register(
        spec(
            "explode",
            ToolResult(
                call_id="ignored",
                tool="explode",
                ok=False,
                control="fail",
                error="boom",
            ),
        )
    )
    model = FakeModel([call("explode")])

    runtime, policy, _ = runtime_for(registry, model)
    result = asyncio.run(runtime.run_turn(state=make_state(), policy=policy))

    assert result.status == "failed"
    assert result.error == "boom"


def test_step_cap_returns_max_steps():
    registry = ToolRegistry()
    registry.register(
        spec(
            "search",
            ToolResult(call_id="ignored", tool="search", ok=True, control="continue"),
        )
    )
    model = FakeModel([call("search"), call("search"), call("search")])

    runtime, policy, _ = runtime_for(registry, model, max_steps=2)
    result = asyncio.run(runtime.run_turn(state=make_state(), policy=policy))

    assert result.status == "max_steps"
    assert result.steps == 2
    assert model.calls == 2


def test_terminal_control_still_answers_all_parallel_tool_calls():
    calls = []
    registry = ToolRegistry()

    async def finish_handler(_args, _context):
        return ToolResult(
            call_id="",
            tool="finish",
            ok=True,
            control="finish",
            output={"reply": "done"},
        )

    async def search_handler(_args, _context):
        calls.append("search")
        return ToolResult(call_id="", tool="search", ok=True)

    registry.register(
        ToolSpec(
            name="finish",
            description="finish",
            args_model=EmptyArgs,
            handler=finish_handler,
        )
    )
    registry.register(
        ToolSpec(
            name="search",
            description="search",
            args_model=EmptyArgs,
            handler=search_handler,
        )
    )
    ai = AIMessage(
        content="",
        tool_calls=[
            {"name": "finish", "args": {}, "id": "call-a", "type": "tool_call"},
            {"name": "search", "args": {}, "id": "call-b", "type": "tool_call"},
            {"name": "search", "args": {}, "id": "call-c", "type": "tool_call"},
        ],
    )
    model = FakeModel([ai])

    runtime, policy, _ = runtime_for(registry, model)
    state = make_state()
    result = asyncio.run(runtime.run_turn(state=state, policy=policy))

    assert result.status == "done"
    tool_messages = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert {m.tool_call_id for m in tool_messages} == {"call-a", "call-b", "call-c"}
    assert calls == []


def test_parallel_non_terminal_tool_calls_all_execute():
    calls = []
    registry = ToolRegistry()

    async def handler(_args, _context):
        calls.append(1)
        return ToolResult(call_id="", tool="search", ok=True)

    registry.register(
        ToolSpec(
            name="search",
            description="search",
            args_model=EmptyArgs,
            handler=handler,
        )
    )
    ai = AIMessage(
        content="",
        tool_calls=[
            {"name": "search", "args": {}, "id": "a", "type": "tool_call"},
            {"name": "search", "args": {}, "id": "b", "type": "tool_call"},
        ],
    )
    model = FakeModel([ai, AIMessage(content="done")])

    runtime, policy, _ = runtime_for(registry, model)
    state = make_state()
    result = asyncio.run(runtime.run_turn(state=state, policy=policy))

    assert result.status == "done"
    assert len(calls) == 2
    tool_messages = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert {m.tool_call_id for m in tool_messages} == {"a", "b"}


def test_parallel_safe_tools_run_concurrently():
    """fetch_detail batches must overlap in wall time."""
    import time

    starts: list[float] = []
    ends: list[float] = []
    registry = ToolRegistry()

    async def handler(_args, _context):
        starts.append(time.monotonic())
        await asyncio.sleep(0.15)
        ends.append(time.monotonic())
        return ToolResult(call_id="", tool="fetch_detail", ok=True)

    registry.register(
        ToolSpec(
            name="fetch_detail",
            description="fetch_detail",
            args_model=EmptyArgs,
            handler=handler,
            parallel_safe=True,
        )
    )
    ai = AIMessage(
        content="",
        tool_calls=[
            {"name": "fetch_detail", "args": {}, "id": "d1", "type": "tool_call"},
            {"name": "fetch_detail", "args": {}, "id": "d2", "type": "tool_call"},
        ],
    )
    model = FakeModel([ai, AIMessage(content="done")])
    runtime, policy, _ = runtime_for(registry, model)
    state = make_state()
    result = asyncio.run(runtime.run_turn(state=state, policy=policy))
    assert result.status == "done"
    assert len(starts) == 2 and len(ends) == 2
    # Both calls started before either finished ⇒ concurrent.
    assert max(starts) < min(ends)


def test_unknown_tool_is_fed_back_and_does_not_crash():
    model = FakeModel([call("missing"), AIMessage(content="recovered")])

    runtime, policy, _ = runtime_for(ToolRegistry(), model)
    state = make_state()
    result = asyncio.run(runtime.run_turn(state=state, policy=policy))

    assert result.status == "done"
    assert result.output == {"text": "recovered"}
    assert state["tool_results"][0]["ok"] is False
    assert state["tool_results"][0]["error"] == "unknown_tool:missing"


def test_resume_appends_user_input_and_continues():
    registry = ToolRegistry()
    registry.register(
        spec(
            "ask_user",
            ToolResult(
                call_id="ignored",
                tool="ask_user",
                ok=True,
                control="wait_user",
                output={"question": "budget?", "reason": "need_user"},
            ),
        )
    )
    model = FakeModel([call("ask_user"), AIMessage(content="after resume")])
    runtime, policy, _ = runtime_for(registry, model)
    state = make_state()

    first = asyncio.run(runtime.run_turn(state=state, policy=policy))
    assert first.status == "waiting_user"

    second = asyncio.run(
        runtime.resume(state=state, user_input="5000", policy=policy)
    )
    assert second.status == "done"
    assert second.output == {"text": "after resume"}
    assert any(
        isinstance(m, HumanMessage) and m.content == "5000" for m in state["messages"]
    )


def test_react_policy_delegates_to_model():
    from app.agent.policies.react import ReActPolicy

    model = FakeModel([AIMessage(content="ok")])
    policy = ReActPolicy(model=model)

    message = asyncio.run(policy.next_action(make_state(), [SystemMessage(content="s")]))

    assert message.content == "ok"
    assert model.calls == 1
