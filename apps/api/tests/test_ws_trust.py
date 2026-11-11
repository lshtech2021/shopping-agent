import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import settings
from app.main import app
from app.ws_hub import ExtensionHub

client = TestClient(app)


def test_ws_rejects_missing_token(monkeypatch):
    monkeypatch.setattr(settings, "extension_token", "secret")
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/extension") as ws:
            ws.receive_json()


def test_ws_rejects_wrong_token(monkeypatch):
    monkeypatch.setattr(settings, "extension_token", "secret")
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/ws/extension?token=wrong",
            headers={"origin": "chrome-extension://extension-id"},
        ) as ws:
            ws.receive_json()


def test_ws_accepts_matching_token_and_extension_id(monkeypatch):
    monkeypatch.setattr(settings, "extension_token", "secret")
    monkeypatch.setattr(settings, "extension_id", "extension-id")
    with client.websocket_connect(
        "/ws/extension?token=secret",
        headers={"origin": "chrome-extension://extension-id"},
    ) as ws:
        ws.send_json({"type": "ping", "ts": 1})
        assert ws.receive_json()["type"] == "pong"


def test_ws_rejects_other_extension_origin(monkeypatch):
    monkeypatch.setattr(settings, "extension_token", "secret")
    monkeypatch.setattr(settings, "extension_id", "extension-id")
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/ws/extension?token=secret",
            headers={"origin": "chrome-extension://other-extension"},
        ) as ws:
            ws.receive_json()


def test_rest_requires_extension_token_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "extension_token", "secret")

    denied = client.post(
        "/api/v1/sessions",
        json={"requirement": "笔记本", "site": "jd"},
    )
    assert denied.status_code == 403

    allowed = client.post(
        "/api/v1/sessions",
        json={"requirement": "笔记本", "site": "jd"},
        headers={"X-Extension-Token": "secret"},
    )
    assert allowed.status_code == 200


def test_reconnected_extension_can_redeliver_a_pending_job_result():
    async def scenario():
        hub = ExtensionHub()

        class FakeWS:
            async def send_json(self, _payload):
                return None

        hub._connections["old"] = FakeWS()
        hub._connections["new"] = FakeWS()
        fut = asyncio.get_running_loop().create_future()
        hub._pending["job-1"] = fut
        hub._pending_conn["job-1"] = "old"
        hub._connections.pop("old")

        reply = await hub.handle_message(
            "new",
            {
                "type": "job_result",
                "job_id": "job-1",
                "result": {"status": "ok", "items": [1]},
            },
        )

        assert reply["type"] == "ack"
        assert await fut == {"status": "ok", "items": [1]}

    asyncio.run(scenario())


def test_job_result_from_wrong_connection_is_rejected():
    async def scenario():
        hub = ExtensionHub()

        class FakeWS:
            async def send_json(self, _payload):
                return None

        hub._connections["A"] = FakeWS()
        hub._connections["B"] = FakeWS()
        fut = asyncio.get_running_loop().create_future()
        hub._pending["job-1"] = fut
        hub._pending_conn["job-1"] = "A"

        reply = await hub.handle_message(
            "B",
            {
                "type": "job_result",
                "job_id": "job-1",
                "result": {"status": "ok"},
            },
        )
        assert reply["type"] == "error"
        assert reply["error"] == "job_owner_mismatch"
        assert not fut.done()

    asyncio.run(scenario())
