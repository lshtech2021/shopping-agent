from fastapi.testclient import TestClient

from app.main import app
from app.store import Session, SessionStore, store

client = TestClient(app)


def test_runs_table_persists_session_settings():
    from app import store_pg

    assert "settings" in store_pg._ALL_COLUMNS
    assert "settings" in store_pg._JSON_COLUMNS


def test_create_sets_singular_site_settings(tmp_path, monkeypatch):
    store = SessionStore("memory")
    session = store.create("买笔记本", "jd")
    assert session.settings["site"] == "jd"
    assert session.settings.get("currency") == "CNY"
    assert session.sites == ["jd"]  # derived mirror for one release of callers


def test_create_accepts_currency_before_first_message():
    created = client.post(
        "/api/v1/sessions",
        json={"requirement": "笔记本", "site": "ebay", "currency": "eur"},
    ).json()
    assert created["settings"]["site"] == "ebay"
    assert created["settings"]["currency"] == "EUR"
    assert created["settings"]["currency_source"] == "manual"


def test_create_rejects_unsupported_currency():
    response = client.post(
        "/api/v1/sessions",
        json={"requirement": "笔记本", "site": "ebay", "currency": "BTC"},
    )
    assert response.status_code == 422


def test_create_without_currency_keeps_site_default():
    store = SessionStore("memory")
    jd = store.create("笔记本", "jd")
    assert jd.settings["currency"] == "CNY"
    assert jd.settings["currency_source"] == "detected"
    assert jd.settings["max_shipping_ratio"] == 0.10
    assert jd.settings["max_shipping_cost"] is None
    ebay = store.create("笔记本", "ebay")
    assert ebay.settings["currency"] is None
    assert ebay.settings["currency_source"] is None


def test_create_accepts_shipping_limits():
    created = client.post(
        "/api/v1/sessions",
        json={
            "requirement": "笔记本",
            "site": "ebay",
            "currency": "CNY",
            "max_shipping_ratio": 0.2,
            "max_shipping_cost": 100,
        },
    ).json()
    assert created["settings"]["max_shipping_ratio"] == 0.2
    assert created["settings"]["max_shipping_cost"] == 100


def test_create_rejects_invalid_shipping_ratio():
    response = client.post(
        "/api/v1/sessions",
        json={
            "requirement": "笔记本",
            "site": "ebay",
            "max_shipping_ratio": 1.5,
        },
    )
    assert response.status_code == 422


def test_patch_shipping_limits_clears_recommendations_and_allows_null():
    created = client.post(
        "/api/v1/sessions",
        json={"requirement": "笔记本", "site": "ebay"},
    ).json()
    sid = created["session_id"]
    session = store.get(sid)
    session.recommendations = [
        {"product_id": "ebay:1", "title": "Laptop", "reason": "fit"}
    ]
    store.touch(session, durable=True)

    response = client.patch(
        f"/api/v1/sessions/{sid}/settings",
        json={"max_shipping_cost": 100},
    )
    body = response.json()
    assert body["recommendations_cleared"] is True
    assert body["settings"]["max_shipping_cost"] == 100
    assert store.get(sid).recommendations == []

    cleared = client.patch(
        f"/api/v1/sessions/{sid}/settings",
        json={"max_shipping_cost": None},
    ).json()
    assert cleared["settings"]["max_shipping_cost"] is None


def test_from_dict_migrates_legacy_sites_list():
    raw = {
        "id": "abc",
        "requirement": "x",
        "sites": ["ebay", "jd"],
        "requirements": {"budget_cny": 5000, "must_have": []},
        "plan": {
            "metadata": {
                "requirements": {"budget_cny": 5000, "category": "笔记本"}
            }
        },
        "recommendations": [{"product_id": "p1", "price_cny": 999}],
        "products": {},
    }
    session = Session.from_dict(raw)
    assert session.settings["site"] == "ebay"  # first entry
    assert session.requirements.get("budget") == 5000
    assert "budget_cny" not in session.requirements
    assert session.plan["metadata"]["requirements"]["budget"] == 5000
    assert "budget_cny" not in session.plan["metadata"]["requirements"]
    assert "price_cny" not in session.recommendations[0]


def test_list_sessions_includes_settings_site():
    created = client.post(
        "/api/v1/sessions",
        json={"requirement": "笔记本", "site": "ebay"},
    ).json()
    sid = created["session_id"]
    listed = client.get("/api/v1/sessions").json()
    match = next(item for item in listed if item["session_id"] == sid)
    assert match["settings"]["site"] == "ebay"


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


def test_patch_site_clears_site_keyed_state_without_resuming_done_session():
    created = client.post(
        "/api/v1/sessions",
        json={"requirement": "笔记本", "site": "jd"},
    ).json()
    sid = created["session_id"]
    session = store.get(sid)
    session.status = "done"
    session.plan = {"metadata": {"search_queries": {"jd": ["开发笔记本"]}}}
    session.searched_sites = ["jd"]
    session.products["jd:1"] = {
        "product_id": "jd:1",
        "source": "jd",
        "source_product_id": "1",
        "title": "Laptop",
        "price": 5000,
        "currency": "CNY",
    }
    session.recommendations = [
        {"product_id": "jd:1", "title": "Laptop", "reason": "fit"}
    ]
    store.touch(session, durable=True)

    response = client.patch(
        f"/api/v1/sessions/{sid}/settings",
        json={"site": "ebay"},
    )

    assert response.status_code == 200
    detail = client.get(f"/api/v1/sessions/{sid}").json()
    assert detail["status"] == "done"
    assert detail["settings"]["site"] == "ebay"
    assert detail["plan"] is None
    assert detail["searched_sites"] == []
    assert [item["product_id"] for item in detail["products"]] == ["jd:1"]
    assert detail["recommendations"] == []


def test_patch_clear_products_adopts_detected_currency():
    created = client.post(
        "/api/v1/sessions",
        json={"requirement": "笔记本", "site": "ebay"},
    ).json()
    sid = created["session_id"]
    session = store.get(sid)
    session.products["ebay:1"] = {
        "product_id": "ebay:1",
        "source": "ebay",
        "source_product_id": "1",
        "title": "Laptop",
        "price": 500,
        "currency": "USD",
    }
    session.recommendations = [
        {"product_id": "ebay:1", "title": "Laptop", "reason": "fit"}
    ]
    store.touch(session, durable=True)

    response = client.patch(
        f"/api/v1/sessions/{sid}/settings",
        json={
            "currency": "CAD",
            "currency_source": "detected",
            "clear_products": True,
        },
    )

    assert response.status_code == 200
    detail = client.get(f"/api/v1/sessions/{sid}").json()
    assert detail["products"] == []
    assert detail["recommendations"] == []
    assert detail["settings"]["currency"] == "CAD"
    assert detail["settings"]["currency_source"] == "detected"
