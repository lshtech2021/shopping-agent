from __future__ import annotations

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage

from app.agent.context import ContextBuilder


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
