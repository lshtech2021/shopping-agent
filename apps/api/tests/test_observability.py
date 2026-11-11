"""Agent execution audit events and log-noise controls."""
from __future__ import annotations

import logging
import time

from app.agent_runner import _turn_summary
from app.logging_setup import _AccessPollingFilter
from app.store import store
from app.trace import classify_error, log_event


def test_classify_error_maps_provider_and_tool_failures():
    assert (
        classify_error(
            "error",
            "Error code: 400 - This response_format type is unavailable now",
        )
        == "provider_structured_output_unsupported"
    )
    assert classify_error("blocked", "currency_mismatch") == "currency_mismatch"
    assert classify_error("parse_error", "detail_not_found") == "detail_not_found"
    assert classify_error("error", "job timeout") == "timeout"
    assert classify_error("ok", None) is None


def test_access_polling_filter_drops_heartbeat_gets():
    access_filter = _AccessPollingFilter()

    def record(method, path):
        return logging.LogRecord(
            "uvicorn.access",
            logging.INFO,
            "",
            0,
            '%s - "%s %s HTTP/%s" %d',
            ("127.0.0.1:1", method, path, "1.1", 200),
            None,
        )

    assert not access_filter.filter(
        record("GET", "/api/v1/sessions/abc/events?after=1&limit=100")
    )
    assert not access_filter.filter(record("GET", "/api/v1/sessions/abc"))
    assert access_filter.filter(record("POST", "/api/v1/sessions/abc/messages"))
    assert access_filter.filter(record("GET", "/api/v1/sessions"))


def test_turn_summary_aggregates_llm_tools_and_exclusions():
    session = store.create("测试", "jd")
    session.turn = 1
    log_event(
        session,
        "LLM 调用",
        stage="llm.call",
        source="llm",
        data={
            "model": "test-model",
            "prompt_tokens": 10,
            "completion_tokens": 2,
            "total_tokens": 12,
        },
    )
    log_event(
        session,
        "工具 query_inventory → ok",
        stage="llm.tool.query_inventory",
        source="llm",
        data={
            "status": "ok",
            "result": {
                "count": 1,
                "excluded": {"parts": 3, "shipping": 2},
            },
        },
    )
    log_event(
        session,
        "工具 recommend → ok",
        stage="llm.tool.recommend",
        source="llm",
        data={"status": "ok"},
    )

    _turn_summary(
        session,
        {
            "status": "done",
            "recommendations": [{"product_id": "p1"}],
        },
        started=time.perf_counter(),
        product_count_before=0,
    )

    summary = session.execution_logs[-1]
    assert summary["stage"] == "turn.summary"
    data = summary["data"]
    assert data["llm"]["model"] == "test-model"
    assert data["llm"]["total_tokens"] == 12
    assert data["tools"]["query_inventory"] == 1
    assert data["tools"]["recommend"] == 1
    assert data["inventory"]["excluded"] == {"parts": 3, "shipping": 2}
    assert data["recommendations"] == 1
    assert data["versions"]["llm_model"] == "test-model"
