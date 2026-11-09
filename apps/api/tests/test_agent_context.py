from __future__ import annotations

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from app.agent.context import ContextBuilder, _estimate_tokens


def _assistant_call(*call_ids: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "parse_listing",
                "args": {},
                "id": call_id,
                "type": "tool_call",
            }
            for call_id in call_ids
        ],
    )


def _state(messages, tool_results=None):
    return {
        "run_id": "run-1",
        "messages": messages,
        "plan": None,
        "step": 1,
        "phase": "executing",
        "tool_results": tool_results or [],
        "pending_user": None,
        "error": None,
    }


def _text(messages: list[BaseMessage]) -> str:
    return "\n".join(
        str(message.content) for message in messages if isinstance(message, BaseMessage)
    )


def test_build_keeps_system_prompt_free_of_raw_page_titles():
    messages = [
        HumanMessage(content="find a laptop"),
        _assistant_call("call-1"),
        ToolMessage(
            content='{"items": [{"title": "SECRET PRODUCT TITLE"}]}',
            tool_call_id="call-1",
            name="parse_listing",
        ),
    ]
    builder = ContextBuilder(max_tokens=12000)

    built = builder.build(
        system_prompt="SYSTEM POLICY",
        state=_state(messages),
        memory_summary="MEMORY SUMMARY",
        tool_specs=[],
    )

    assert isinstance(built[0], SystemMessage)
    assert "SYSTEM POLICY" in built[0].content
    assert "MEMORY SUMMARY" in built[0].content
    assert "SECRET PRODUCT TITLE" not in built[0].content
    tool_messages = [message for message in built if isinstance(message, ToolMessage)]
    assert tool_messages
    assert "[UNTRUSTED_PAGE_DATA]" in tool_messages[0].content


def test_build_keeps_only_recent_ten_messages():
    messages = [HumanMessage(content=f"message-{index}") for index in range(15)]
    builder = ContextBuilder(max_tokens=12000)

    built = builder.build(
        system_prompt="SYSTEM",
        state=_state(messages),
        memory_summary="",
        tool_specs=[],
    )

    text = _text(built)
    assert "message-5" in text
    assert "message-14" in text
    assert "message-4" not in text


def test_build_includes_unfinished_tool_result_as_untrusted_data():
    builder = ContextBuilder(max_tokens=12000)
    tool_results = [
        {
            "call_id": "call-9",
            "tool": "search",
            "ok": True,
            "data": {"title": "PENDING TITLE"},
            "control": "continue",
        }
    ]

    built = builder.build(
        system_prompt="SYSTEM",
        state=_state([], tool_results),
        memory_summary="",
        tool_specs=[],
    )

    assert any(
        "[UNTRUSTED_PAGE_DATA]" in str(message.content)
        and "PENDING TITLE" in str(message.content)
        for message in built[1:]
    )


def test_long_tool_result_is_truncated_to_token_budget():
    long_result = "x" * 40000
    messages = [
        HumanMessage(content="latest question"),
        _assistant_call("call-1"),
        ToolMessage(
            content=long_result,
            tool_call_id="call-1",
            name="parse_listing",
        ),
    ]
    builder = ContextBuilder(max_tokens=500)

    built = builder.build(
        system_prompt="SYSTEM",
        state=_state(messages),
        memory_summary="",
        tool_specs=[],
    )

    text = _text(built)
    assert "latest question" in text
    assert len(text) <= 500 * 4 + 200
    assert "[truncated]" in text


def test_old_tool_result_is_summarized_before_dropping_latest_question():
    messages = [
        _assistant_call("call-old"),
        ToolMessage(
            content="OLD " * 5000,
            tool_call_id="call-old",
            name="parse_listing",
        ),
        HumanMessage(content="middle question"),
        HumanMessage(content="latest question"),
    ]
    builder = ContextBuilder(max_tokens=300)

    built = builder.build(
        system_prompt="SYSTEM",
        state=_state(messages),
        memory_summary="",
        tool_specs=[],
    )

    text = _text(built)
    assert "latest question" in text
    assert "OLD OLD OLD OLD OLD OLD" not in text


def _assert_valid_transcript(messages: list[BaseMessage]) -> None:
    open_calls: set[str] = set()
    for message in messages:
        if isinstance(message, AIMessage) and getattr(message, "tool_calls", None):
            assert not open_calls, f"unanswered tool calls: {open_calls}"
            open_calls = {call["id"] for call in message.tool_calls}
        elif isinstance(message, ToolMessage):
            assert message.tool_call_id in open_calls, (
                f"orphan tool message {message.tool_call_id}"
            )
            open_calls.remove(message.tool_call_id)
        else:
            assert not open_calls, f"unanswered tool calls: {open_calls}"
    assert not open_calls, f"unanswered tool calls at end: {open_calls}"


def test_tool_groups_stay_paired_across_many_steps_and_long_messages():
    messages: list[BaseMessage] = [HumanMessage(content="goal")]
    for step in range(4):
        first, second = f"{step}-a", f"{step}-b"
        messages.extend(
            [
                _assistant_call(first, second),
                ToolMessage(
                    content=f"result-a-{step}", tool_call_id=first, name="search"
                ),
                ToolMessage(
                    content=f"result-b-{step}", tool_call_id=second, name="search"
                ),
                AIMessage(content="A" * 12000),
            ]
        )
    builder = ContextBuilder(max_tokens=12000)

    built = builder.build(
        system_prompt="SYSTEM",
        state=_state(messages),
        memory_summary="",
        tool_specs=[],
    )

    _assert_valid_transcript(built)
    assert any("result-b-3" in str(message.content) for message in built)


def test_current_step_parallel_tool_results_are_not_summarized():
    messages = [
        HumanMessage(content="search both sites"),
        _assistant_call("a", "b"),
        ToolMessage(content="JD RESULT", tool_call_id="a", name="search"),
        ToolMessage(content="EBAY RESULT", tool_call_id="b", name="search"),
    ]
    builder = ContextBuilder(max_tokens=12000)

    built = builder.build(
        system_prompt="SYSTEM",
        state=_state(messages),
        memory_summary="",
        tool_specs=[],
    )

    text = _text(built)
    assert "JD RESULT" in text
    assert "EBAY RESULT" in text
    assert "tool result summarized" not in text


def test_tight_budget_always_keeps_latest_user_message():
    messages = [
        HumanMessage(content="old " * 200),
        HumanMessage(content="latest request"),
    ]
    builder = ContextBuilder(max_tokens=50)

    built = builder.build(
        system_prompt="SYSTEM " * 100,
        state=_state(messages),
        memory_summary="",
        tool_specs=[],
    )

    text = _text(built)
    assert "latest request" in text
    assert sum(_estimate_tokens(str(message.content)) for message in built) <= 50


def test_cjk_text_is_counted_as_roughly_one_token_per_character():
    assert _estimate_tokens("这是一个中文测试") >= 8
    assert _estimate_tokens("abcdefgh") == 2
