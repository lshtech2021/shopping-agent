"""Budget amounts are always in session settings.currency."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.store import store
from app.tools import ToolBox

client = TestClient(app)


def test_budget_requires_session_currency():
    session = store.create("预算 5000 的笔记本", "ebay")
    session.settings["currency"] = None
    session.settings["currency_source"] = None

    result = ToolBox(session).update_requirements(budget=5000)

    assert result["requirements"]["budget"] is None
    assert "budget_currency" not in result["requirements"]
    assert result["needs_clarify"] is True
    assert result["clarify_question"]["field"] == "budget"


def test_unlimited_upper_keeps_a_confirmed_lower_bound():
    session = store.create("预算 5000 到 8000 的笔记本", "ebay")
    session.settings["currency"] = "CNY"
    session.requirements.update(
        {
            "budget": 8000,
            "budget_min": 5000,
            "budget_unlimited": False,
        }
    )

    result = ToolBox(session).update_requirements(
        budget_unlimited=True,
        budget_min=5000,
    )

    assert result["requirements"]["budget"] is None
    assert result["requirements"]["budget_min"] == 5000
    assert "budget_currency" not in result["requirements"]
    assert result["requirements"]["budget_unlimited"] is True


def test_jd_budget_uses_session_currency():
    session = store.create("预算 5000 的笔记本", "jd")

    result = ToolBox(session).update_requirements(budget=5000)

    assert result["requirements"]["budget"] == 5000
    assert "budget_currency" not in result["requirements"]
    assert session.settings["currency"] == "CNY"


def test_budget_writes_when_session_currency_set():
    session = store.create("预算 5000 的笔记本", "ebay")
    session.settings["currency"] = "USD"
    session.settings["currency_source"] = "manual"

    result = ToolBox(session).update_requirements(budget=500)

    assert result["requirements"]["budget"] == 500
    assert "budget_currency" not in result["requirements"]
    assert result["needs_clarify"] is False


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
    assert "budget_currency" not in detail["requirements"]
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


def test_normalize_strips_legacy_budget_currency():
    from app.store import Session

    session = Session.from_dict(
        {
            "id": "legacy",
            "requirement": "x",
            "sites": ["ebay"],
            "settings": {"site": "ebay", "currency": "USD"},
            "requirements": {
                "budget": 100,
                "budget_currency": "USD",
                "must_have": [],
            },
            "products": {},
        }
    )
    assert session.requirements.get("budget") == 100
    assert "budget_currency" not in session.requirements
