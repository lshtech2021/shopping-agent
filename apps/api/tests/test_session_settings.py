from fastapi.testclient import TestClient

from app.main import app
from app.store import Session, SessionStore

client = TestClient(app)


def test_create_sets_singular_site_settings(tmp_path, monkeypatch):
    store = SessionStore("memory")
    session = store.create("买笔记本", "jd")
    assert session.settings["site"] == "jd"
    assert session.settings.get("currency") is None
    assert session.sites == ["jd"]  # derived mirror for one release of callers


def test_from_dict_migrates_legacy_sites_list():
    raw = {
        "id": "abc",
        "requirement": "x",
        "sites": ["ebay", "jd"],
        "requirements": {"budget_cny": 5000, "must_have": []},
        "products": {},
    }
    session = Session.from_dict(raw)
    assert session.settings["site"] == "ebay"  # first entry
    assert session.requirements.get("budget") == 5000
    assert "budget_cny" not in session.requirements


def test_patch_settings_changes_site():
    created = client.post(
        "/api/v1/sessions",
        json={"requirement": "笔记本", "site": "jd"},
    ).json()
    sid = created["session_id"]
    resp = client.patch(
        f"/api/v1/sessions/{sid}/settings",
        json={"site": "ebay", "currency": "USD"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["settings"]["site"] == "ebay"
    assert body["settings"]["currency"] == "USD"
    assert body["settings"]["currency_source"] == "manual"
