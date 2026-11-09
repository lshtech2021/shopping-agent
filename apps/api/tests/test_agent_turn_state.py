import asyncio

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.config import settings
from app.main import app
from app.store import store
from app.tools import ToolBox

client = TestClient(app)


def seed_session(**overrides):
    session = store.create("测试需求", overrides.pop("sites", ["jd"]))
    requirements = {
        "budget_cny": None,
        "must_have": [],
        "preferences": [],
        "category": None,
        "use_case": None,
    }
    requirements.update(overrides)
    session.requirements = requirements
    return session


def test_search_rejects_site_not_selected():
    session = seed_session(sites=["jd"])

    result = asyncio.run(ToolBox(session).search("ebay", "laptop"))

    assert result["status"] == "blocked"
    assert result["error"] == "site_not_selected"


def test_next_page_failure_does_not_advance_page(monkeypatch):
    session = seed_session(sites=["jd"])
    box = ToolBox(session)
    box.page_by_site["jd"] = 1

    async def fake_browse(self, tool, args):
        return {"status": "blocked", "error": "no_extension_online"}

    monkeypatch.setattr(ToolBox, "_browse", fake_browse)

    result = asyncio.run(box.next_page("jd"))

    assert result["error"] == "no_extension_online"
    assert box.page_by_site["jd"] == 1


def test_final_plain_text_at_step_cap_is_returned(fake_llm, monkeypatch):
    monkeypatch.setattr(settings, "agent_max_steps", 1)
    fake_llm([AIMessage(content="最终推荐")])
    session = seed_session()

    response = client.post(
        f"/api/v1/sessions/{session.id}/messages",
        json={"content": "笔记本"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "最终推荐"
    assert body["interrupted"] is False
    assert body["interrupt_reason"] is None
