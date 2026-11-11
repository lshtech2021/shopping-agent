"""Budget amounts must always have an explicit currency."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.store import store
from app.tools import ToolBox

client = TestClient(app)


def test_budget_sets_session_currency_when_stated():
    session = store.create("预算 5000 人民币的笔记本", "ebay")
    session.settings["currency"] = None
    session.settings["currency_source"] = None

    result = ToolBox(session).update_requirements(
        budget=5000, budget_currency="CNY"
    )

    assert result["requirements"]["budget"] == 5000
    assert result["requirements"]["budget_currency"] == "CNY"
    assert session.settings["currency"] == "CNY"
    assert result["needs_clarify"] is False


def test_budget_without_currency_is_not_written():
    session = store.create("预算 5000 的笔记本", "ebay")
    session.settings["currency"] = None
    session.settings["currency_source"] = None

    result = ToolBox(session).update_requirements(budget=5000)

    assert result["requirements"]["budget"] is None
    assert result["requirements"]["budget_currency"] is None
    assert result["needs_clarify"] is True
    assert result["clarify_question"]["field"] == "budget"


def test_budget_currency_must_match_session_currency():
    session = store.create("预算 5000 人民币的笔记本", "ebay")
    session.settings["currency"] = "USD"

    result = ToolBox(session).update_requirements(
        budget=5000, budget_currency="CNY"
    )

    assert result["requirements"]["budget"] is None
    assert result["needs_clarify"] is True
    assert "budget_currency_mismatch" in " ".join(result["parse_notes"])


def test_jd_budget_defaults_to_session_currency():
    session = store.create("预算 5000 的笔记本", "jd")

    result = ToolBox(session).update_requirements(budget=5000)

    assert result["requirements"]["budget_currency"] == "CNY"
    assert session.settings["currency"] == "CNY"


def test_currency_patch_clears_budget_and_plan():
    created = client.post(
        "/api/v1/sessions",
        json={"requirement": "预算 5000 的笔记本", "site": "ebay"},
    ).json()
    sid = created["session_id"]
    session = store.get(sid)
    session.settings["currency"] = "CNY"
    session.settings["currency_source"] = "manual"
    session.requirements["budget"] = 5000
    session.requirements["budget_currency"] = "CNY"
    session.plan = {"metadata": {"search_queries": {"ebay": ["laptop"]}}}
    session.searched_sites = ["ebay"]
    session.recommendations = [{"product_id": "p1"}]
    store.touch(session, durable=True)

    response = client.patch(
        f"/api/v1/sessions/{sid}/settings",
        json={"currency": "USD", "currency_source": "manual"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["budget_cleared"] is True
    assert body["settings"]["currency"] == "USD"
    detail = client.get(f"/api/v1/sessions/{sid}").json()
    assert detail["requirements"]["budget"] is None
    assert detail["requirements"]["budget_currency"] is None
    assert detail["plan"] is None
    assert detail["searched_sites"] == []
    assert detail["recommendations"] == []


def test_currency_patch_rejects_unknown_currency():
    created = client.post(
        "/api/v1/sessions",
        json={"requirement": "笔记本", "site": "ebay"},
    ).json()
    response = client.patch(
        f"/api/v1/sessions/{created['session_id']}/settings",
        json={"currency": "XYZ"},
    )
    assert response.status_code == 422
