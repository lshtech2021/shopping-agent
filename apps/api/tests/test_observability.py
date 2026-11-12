"""Agent execution audit events and log-noise controls."""
from __future__ import annotations

import logging
import time

from fastapi.testclient import TestClient

from app import metrics
from app.agent_runner import _turn_summary
from app.config import settings
from app.domains.shopping.adapter import ShoppingTrace
from app.logging_setup import _AccessPollingFilter
from app.main import app
from app.store import store
from app.trace import classify_error, log_event

client = TestClient(app)


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
        "规划 LLM 调用",
        stage="llm.call",
        source="llm",
        data={
            "component": "planner",
            "model": "test-model",
            "total_tokens": 5,
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
    log_event(
        session,
        "需求评审降级",
        stage="provider.fallback",
        source="llm",
        level="warning",
        data={
            "component": "requirement_review",
            "error_code": "provider_structured_output_unsupported",
        },
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
    assert data["llm"]["total_tokens"] == 17
    assert data["llm"]["components"] == {"main": 1, "planner": 1}
    assert data["llm"]["internal_calls"] == 1
    assert data["llm"]["internal_tokens"] == 5
    assert data["tools"]["query_inventory"] == 1
    assert data["tools"]["recommend"] == 1
    assert data["inventory"]["excluded"] == {"parts": 3, "shipping": 2}
    assert data["recommendations"] == 1
    assert data["llm"]["structured_fallbacks"] == 1
    assert data["versions"]["llm_model"] == "test-model"


def test_get_session_reports_tool_and_session_status_separately():
    from app.tools import ToolBox

    session = store.create("测试", "jd")
    session.status = "running"

    info = ToolBox(session).get_session()

    assert info["status"] == "ok"
    assert info["session_status"] == "running"


def test_turn_summary_counts_structured_fallback_calls():
    session = store.create("测试", "jd")
    session.turn = 1
    log_event(
        session,
        "商品匹配评估",
        stage="llm.call",
        source="llm",
        data={
            "component": "fit_judge",
            "structured": False,
            "total_tokens": 10,
        },
    )

    _turn_summary(
        session,
        {"status": "done", "recommendations": []},
        started=time.perf_counter(),
        product_count_before=0,
    )

    data = session.execution_logs[-1]["data"]
    assert data["llm"]["structured_fallback_calls"] == 1
    assert data["llm"]["structured_fallbacks"] == 1


def test_large_tool_result_is_stored_by_reference(monkeypatch):
    session = store.create("测试", "jd")
    monkeypatch.setattr(settings, "artifact_storage_enabled", True)
    monkeypatch.setattr(settings, "artifact_threshold_chars", 100)
    payload = {
        "status": "ok",
        "items": [{"title": "x" * 500}],
    }

    ShoppingTrace(session).event(
        "llm.tool",
        tool="query_inventory",
        status="ok",
        result=payload,
        duration_ms=1,
    )

    data = session.execution_logs[-1]["data"]
    assert data["payload_ref"]
    assert data["payload_chars"] >= 100
    assert data["result"]["items"] == 1
    artifact = store.get_artifact(data["payload_ref"])
    assert artifact["payload"]["items"][0]["title"] == "x" * 500

    response = client.get(f"/api/v1/artifacts/{data['payload_ref']}")
    assert response.status_code == 200
    assert response.json()["payload"]["status"] == "ok"


def test_artifact_storage_disabled_keeps_inline_result(monkeypatch):
    session = store.create("测试", "jd")
    monkeypatch.setattr(settings, "artifact_storage_enabled", False)
    payload = {"status": "ok", "items": [{"title": "x" * 50}]}

    ShoppingTrace(session).event(
        "llm.tool",
        tool="query_inventory",
        status="ok",
        result=payload,
        duration_ms=1,
    )

    data = session.execution_logs[-1]["data"]
    assert "payload_ref" not in data
    assert data["result"]["items"][0]["title"] == "x" * 50


def test_log_redaction_masks_email_and_phone(monkeypatch):
    session = store.create("测试", "jd")
    monkeypatch.setattr(settings, "log_redaction_enabled", True)
    log_event(
        session,
        "联系信息",
        stage="test.redaction",
        data={
            "text": "mail me at buyer@example.com or call 13800138000",
        },
    )
    text = session.execution_logs[-1]["data"]["text"]
    assert "buyer@example.com" not in text
    assert "13800138000" not in text
    assert "[email]" in text
    assert "[phone]" in text


def test_metrics_endpoint_is_hidden_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "metrics_enabled", False)
    response = client.get("/metrics")
    assert response.status_code == 404


def test_metrics_endpoint_aggregates_turns(monkeypatch):
    monkeypatch.setattr(settings, "metrics_enabled", True)
    metrics.reset()
    metrics.record_turn(
        {
            "status": "done",
            "duration_ms": 1500,
            "llm": {"calls": 2, "prompt_tokens": 10, "completion_tokens": 4},
            "tool_status": {"search:ok": 2, "fetch_detail:ok": 1},
            "inventory": {"excluded": {"shipping": 3}},
            "error_codes": {},
            "recommendations": 1,
        }
    )
    response = client.get("/metrics")
    assert response.status_code == 200
    text = response.text
    assert 'shopping_agent_turns_total{status="done"} 1' in text
    assert "shopping_agent_llm_calls_total 2" in text
    assert (
        'shopping_agent_tool_calls_total{status="ok",tool="search"} 2'
        in text
    )
    assert 'shopping_agent_excluded_total{reason="shipping"} 3' in text
    assert "shopping_agent_turn_duration_seconds_count 1" in text


def test_prune_logs_removes_old_events_and_artifacts():
    store._repo.append_run_event(
        "old-session",
        {"stage": "old"},
        created_at="2000-01-01T00:00:00+00:00",
    )
    store._repo.save_artifact(
        {
            "artifact_id": "old-artifact",
            "run_id": "old-session",
            "kind": "tool.test",
            "payload": {"x": 1},
            "created_at": "2000-01-01T00:00:00+00:00",
        }
    )
    pruned = store.prune_logs(1)
    assert pruned["run_events"] >= 1
    assert pruned["artifacts"] >= 1
    assert store.get_artifact("old-artifact") is None


def test_redaction_does_not_rewrite_product_urls(monkeypatch):
    session = store.create("测试", "jd")
    monkeypatch.setattr(settings, "log_redaction_enabled", True)
    log_event(
        session,
        "URL 与电话",
        stage="test.redaction",
        data={
            "url": "https://www.ebay.com/itm/18712345678",
            "text": "call 13800138000",
        },
    )
    data = session.execution_logs[-1]["data"]
    assert data["url"] == "https://www.ebay.com/itm/18712345678"
    assert data["text"] == "call [phone]"
