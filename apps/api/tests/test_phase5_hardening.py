import asyncio
import time

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from starlette.websockets import WebSocketDisconnect

from app import main
from app.config import settings
from app.main import app
from app.store import store
from app.tools import ToolBox, _throttle
from app.ws_hub import ExtensionHub
from conftest import FakeModel, _call, _last_tool_json  # noqa: F401 (shared with phase6)

client = TestClient(app)

REQ = "帮我挑一台 5000 元内用于开发的笔记本，内存至少 32G"


def _new_session(requirement: str = REQ, sites=("jd",)) -> str:
    return client.post(
        "/api/v1/sessions", json={"requirement": requirement, "sites": list(sites)}
    ).json()["session_id"]


def test_missing_budget_asks_instead_of_false_parsing(mock_shopper):
    sid = _new_session("开发笔记本 内存至少 32G 512G SSD")
    body = client.post(
        f"/api/v1/sessions/{sid}/messages",
        json={"content": "开发笔记本 内存至少 32G 512G SSD"},
    ).json()
    # Spec numbers must not be read as a budget; with no real budget the agent
    # interviews the user instead of guessing.
    assert body["interrupted"] and body["interrupt_reason"] == "clarify"
    assert "budget" in [q["id"] for q in body["clarify"]]
    assert client.get(f"/api/v1/sessions/{sid}").json()["requirements"]["budget_cny"] is None


# --- turn-scoped logs --------------------------------------------------------


def test_response_logs_are_only_for_current_turn(mock_shopper):
    sid = _new_session()
    first = client.post(f"/api/v1/sessions/{sid}/messages", json={"content": REQ}).json()
    second = client.post(
        f"/api/v1/sessions/{sid}/messages", json={"content": "预算改成 4500"}
    ).json()
    assert {e["turn"] for e in first["execution_logs"]} == {1}
    assert {e["turn"] for e in second["execution_logs"]} == {2}
    assert client.get(f"/api/v1/sessions/{sid}").json()["execution_logs"]


# --- risk lockout ------------------------------------------------------------


def test_rate_limited_site_stays_closed_until_resume(mock_shopper):
    sid = _new_session("笔记本 预算5000")
    body = client.post(
        f"/api/v1/sessions/{sid}/messages", json={"content": "笔记本 预算5000 触发限流 rate_limit_me"}
    ).json()
    assert body["interrupted"] and body["interrupt_reason"] == "rate_limited"
    assert client.get(f"/api/v1/sessions/{sid}").json()["blocked_sites"] == {"jd": "rate_limited"}

    session = store.get(sid)
    refused = asyncio.run(ToolBox(session).search("jd", "笔记本"))
    assert refused["status"] == "blocked"
    assert refused["error"] == "site_blocked_until_resume"

    resumed = client.post(f"/api/v1/sessions/{sid}/resume", json={"note": "continue"}).json()
    assert resumed["interrupted"] is False
    assert client.get(f"/api/v1/sessions/{sid}").json()["blocked_sites"] == {}


def test_no_extension_gives_actionable_message(mock_shopper):
    settings.mock_browser = False
    try:
        sid = _new_session("笔记本 预算5000")
        body = client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "笔记本 预算5000"}).json()
    finally:
        settings.mock_browser = True
    assert body["interrupted"]
    assert "插件" in body["reply"]

def test_browse_throttle_spaces_calls(monkeypatch):
    monkeypatch.setattr(settings, "browse_min_interval_s", 0.15)
    monkeypatch.setattr(settings, "browse_jitter_s", 0.0)

    async def two_calls():
        await _throttle("throttle-test-site")
        started = time.monotonic()
        waited = await _throttle("throttle-test-site")
        return waited, time.monotonic() - started

    waited, elapsed = asyncio.run(two_calls())
    assert waited > 0.05 and elapsed >= 0.1


def test_quotas_reset_every_turn_so_followup_research_still_works(mock_shopper):
    sid = _new_session()
    for turn in range(1, 5):  # > the per-turn search quota of 2 in total
        body = client.post(
            f"/api/v1/sessions/{sid}/messages", json={"content": f"{REQ} 重新搜索 第{turn}次"}
        ).json()
        assert body["interrupted"] is False, (turn, body["reply"])
    assert store.get(sid).quotas["jd"]["search"] == 1  # only this turn's usage


def test_guard_refusal_does_not_consume_quota():
    sid = _new_session()
    session = store.get(sid)
    session.blocked_sites["jd"] = "captcha"
    box = ToolBox(session)
    for _ in range(5):
        assert asyncio.run(box.search("jd", "笔记本"))["error"] == "site_blocked_until_resume"
    assert session.quotas.get("jd", {}).get("search", 0) == 0


# --- recommend validation ----------------------------------------------------


def test_recommend_rejects_unknown_product_ids():
    sid = _new_session()
    session = store.get(sid)
    box = ToolBox(session)
    result = box.recommend([{"product_id": "does-not-exist", "reason": "x"}], "hi")
    assert result["ok"] is False and result["error"] == "no_valid_product_ids"
    assert session.recommendations == []
    assert session.status != "done"


# --- concurrency + transport security ----------------------------------------


def test_concurrent_turn_on_same_session_is_rejected():
    sid = _new_session()
    lock = main._lock_for(sid)
    asyncio.run(lock.acquire())  # mark a turn as in flight
    try:
        assert client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "x"}).status_code == 409
        assert client.post(f"/api/v1/sessions/{sid}/resume", json={}).status_code == 409
    finally:
        lock.release()


def test_ws_rejects_foreign_origin_and_accepts_extension():
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/ws/extension", headers={"origin": "https://evil.example"}
        ):
            pass
    with client.websocket_connect(
        "/ws/extension", headers={"origin": "chrome-extension://abcdefg"}
    ) as ws:
        ws.send_json({"type": "ping", "ts": 1})
        assert ws.receive_json()["type"] == "pong"


def test_disconnect_fails_inflight_job_immediately():
    class FakeWS:
        async def send_json(self, _payload):
            return None

    async def scenario():
        hub = ExtensionHub()
        hub.disconnect_grace = 0.1
        hub._connections["c1"] = FakeWS()
        job = asyncio.create_task(hub.dispatch_job("search", {"site": "jd"}, timeout=30))
        await asyncio.sleep(0.05)
        started = time.monotonic()
        await hub.disconnect("c1")
        result = await job
        return result, time.monotonic() - started

    result, elapsed = asyncio.run(scenario())
    assert result["error"] == "extension_disconnected"
    assert elapsed < 2


# --- LangGraph tool-loop policy ----------------------------------------------


def _recommend_first_item(messages):
    items = _last_tool_json(messages)["items"]
    return _call(
        "recommend",
        picks=[{"product_id": items[0]["product_id"], "reason": "4899 元，32G 内存，适合开发"}],
        answer="推荐 ThinkPad T14。",
    )


def test_llm_policy_full_loop(fake_llm):
    model = fake_llm(
        [
            _call("update_requirements", budget_cny=5000, use_case="开发", must_have=["32G内存"]),
            _call("search", site="jd", query="笔记本 开发"),
            _call("scrape_listing", site="jd"),
            _call("query_inventory", limit=5),
            _recommend_first_item,
        ]
    )
    sid = _new_session()
    body = client.post(f"/api/v1/sessions/{sid}/messages", json={"content": REQ}).json()
    assert body["interrupted"] is False
    assert len(body["recommendations"]) == 1
    assert "ThinkPad" in body["recommendations"][0]["title"]
    assert model.calls == 5
    stages = [e["stage"] for e in body["execution_logs"]]
    assert "llm.step" in stages and "llm.tool.search" in stages
    assert client.get("/health").json()["llm_enabled"] is True


def test_llm_policy_risk_result_interrupts_without_more_model_calls(fake_llm):
    model = fake_llm(
        [
            _call("search", site="jd", query="rate_limit_me"),
            _call("search", site="jd", query="should never run"),
        ]
    )
    sid = _new_session("笔记本")
    body = client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "笔记本"}).json()
    assert body["interrupted"] and body["interrupt_reason"] == "rate_limited"
    assert model.calls == 1
    assert store.get(sid).blocked_sites == {"jd": "rate_limited"}


def test_llm_policy_bad_recommend_is_fed_back_not_accepted(fake_llm):
    def check_error_then_query(messages):
        assert _last_tool_json(messages)["error"] == "no_valid_product_ids"
        return _call("query_inventory", limit=5)

    model = fake_llm(
        [
            _call("search", site="jd", query="笔记本"),
            _call("scrape_listing", site="jd"),
            _call("recommend", picks=[{"product_id": "hallucinated", "reason": "x"}], answer="a"),
            check_error_then_query,
            _recommend_first_item,
        ]
    )
    sid = _new_session()
    body = client.post(f"/api/v1/sessions/{sid}/messages", json={"content": REQ}).json()
    assert body["interrupted"] is False and len(body["recommendations"]) == 1
    assert model.calls == 5


def test_llm_policy_plain_text_becomes_clarifying_question(fake_llm):
    fake_llm([AIMessage(content="请问你的预算大概是多少？")])
    sid = _new_session("笔记本")
    body = client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "笔记本"}).json()
    assert body["interrupted"] and body["interrupt_reason"] == "clarify"
    assert "预算" in body["reply"]


def test_llm_policy_step_limit(fake_llm, monkeypatch):
    monkeypatch.setattr(settings, "agent_max_steps", 3)
    model = fake_llm([_call("get_session") for _ in range(6)])
    sid = _new_session("笔记本")
    body = client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "笔记本"}).json()
    assert body["interrupt_reason"] == "step_limit"
    assert model.calls == 3


def test_llm_failure_is_reported_not_500(fake_llm):
    def boom(_messages):
        raise RuntimeError("upstream 401")

    fake_llm([boom])
    sid = _new_session("笔记本")
    response = client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "笔记本"})
    assert response.status_code == 200
    body = response.json()
    assert body["interrupt_reason"] == "agent_error" and body["interrupted"]
    assert store.get(sid).status == "interrupted"
