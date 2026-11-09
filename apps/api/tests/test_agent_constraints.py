from app.config import settings
from app.store import store
from app.tools import ToolBox


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


def seed_product(product_id, **overrides):
    product = {
        "product_id": product_id,
        "source": "jd",
        "source_product_id": product_id,
        "title": "商品",
        "price": 100.0,
        "currency": "CNY",
        "price_cny": 100.0,
        "url": f"https://item.jd.com/{product_id}.html",
        "detail_fetched": True,
        "attributes": {},
    }
    product.update(overrides)
    return product


def test_recommend_rejects_over_budget_product():
    session = seed_session(budget_cny=5000)
    session.products["p1"] = seed_product("p1", price_cny=9000)
    result = ToolBox(session).recommend(
        [{"product_id": "p1", "reason": "测试"}], "ok"
    )
    assert result["ok"] is False
    assert any(v.startswith("budget") for v in result["violations"])


def test_recommend_rejects_wrong_site_product():
    session = seed_session(sites=["jd"])
    session.products["p1"] = seed_product("p1", source="ebay", price_cny=1000)
    result = ToolBox(session).recommend(
        [{"product_id": "p1", "reason": "测试"}], "ok"
    )
    assert result["ok"] is False
    assert "site" in result["violations"]


def test_recommend_rejects_missing_generic_must_have():
    session = seed_session(must_have=["防水"])
    session.products["p1"] = seed_product(
        "p1", title="普通背包", attributes={}, price_cny=100
    )
    result = ToolBox(session).recommend(
        [{"product_id": "p1", "reason": "测试"}], "ok"
    )
    assert result["ok"] is False
    assert any(v.startswith("must_have") for v in result["violations"])


def test_recommend_accepts_must_have_in_attributes():
    session = seed_session(must_have=["防水"])
    session.products["p1"] = seed_product(
        "p1", title="通勤背包", attributes={"防水": "是"}, price_cny=100
    )
    result = ToolBox(session).recommend(
        [{"product_id": "p1", "reason": "防水通勤包"}], "ok"
    )
    assert result["ok"] is True


def test_recommend_requires_detail_fetched(monkeypatch):
    monkeypatch.setattr(settings, "mock_browser", False)
    session = seed_session()
    session.products["p1"] = seed_product(
        "p1", title="防水背包", detail_fetched=False
    )
    result = ToolBox(session).recommend(
        [{"product_id": "p1", "reason": "测试"}], "ok"
    )
    assert result["ok"] is False
    assert "detail" in result["violations"]


def test_query_inventory_excludes_ineligible_products():
    session = seed_session(budget_cny=1000, must_have=["防水"])
    session.products["over"] = seed_product("over", price_cny=2000)
    session.products["wrong"] = seed_product("wrong", source="ebay")
    session.products["missing"] = seed_product("missing", title="普通背包")
    session.products["valid"] = seed_product("valid", title="防水背包")

    items = ToolBox(session).query_inventory(limit=10)["items"]
    assert {item["product_id"] for item in items} == {"valid"}

    detail_items = ToolBox(session).query_inventory(
        limit=10, prefer_detail=True
    )["items"]
    assert {item["product_id"] for item in detail_items} == {"valid"}


def test_query_inventory_prefer_detail_excludes_unfetched():
    session = seed_session()
    session.products["p1"] = seed_product(
        "p1", title="防水背包", detail_fetched=False
    )
    items = ToolBox(session).query_inventory(limit=10, prefer_detail=True)["items"]
    assert items == []
