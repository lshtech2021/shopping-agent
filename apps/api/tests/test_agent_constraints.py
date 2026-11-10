from app.config import settings
from app.store import store
from app.tools import ToolBox
from conftest import judged


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
    result = judged(ToolBox(session)).recommend(
        [{"product_id": "p1", "reason": "测试"}], "ok"
    )
    assert result["ok"] is False
    assert any(v.startswith("budget") for v in result["violations"])


def test_recommend_rejects_wrong_site_product():
    session = seed_session(sites=["jd"])
    session.products["p1"] = seed_product("p1", source="ebay", price_cny=1000)
    result = judged(ToolBox(session)).recommend(
        [{"product_id": "p1", "reason": "测试"}], "ok"
    )
    assert result["ok"] is False
    assert "site" in result["violations"]


def test_recommend_rejects_missing_generic_must_have():
    session = seed_session(must_have=["防水"])
    session.products["p1"] = seed_product(
        "p1", title="普通背包", attributes={}, price_cny=100
    )
    result = judged(ToolBox(session)).recommend(
        [{"product_id": "p1", "reason": "测试"}], "ok"
    )
    assert result["ok"] is False
    assert any(v.startswith("must_have") for v in result["violations"])


def test_recommend_accepts_must_have_in_attributes():
    session = seed_session(must_have=["防水"])
    session.products["p1"] = seed_product(
        "p1", title="通勤背包", attributes={"防水": "是"}, price_cny=100
    )
    result = judged(ToolBox(session)).recommend(
        [{"product_id": "p1", "reason": "防水通勤包"}], "ok"
    )
    assert result["ok"] is True


def test_recommend_requires_detail_fetched(monkeypatch):
    monkeypatch.setattr(settings, "mock_browser", False)
    session = seed_session()
    session.products["p1"] = seed_product(
        "p1", title="防水背包", detail_fetched=False
    )
    result = judged(ToolBox(session)).recommend(
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

    items = judged(ToolBox(session)).query_inventory(limit=10)["items"]
    assert {item["product_id"] for item in items} == {"valid"}

    detail_items = judged(ToolBox(session)).query_inventory(
        limit=10, prefer_detail=True
    )["items"]
    assert {item["product_id"] for item in detail_items} == {"valid"}


def test_query_inventory_prefer_detail_excludes_unfetched():
    session = seed_session()
    session.products["p1"] = seed_product(
        "p1", title="防水背包", detail_fetched=False
    )
    items = judged(ToolBox(session)).query_inventory(limit=10, prefer_detail=True)["items"]
    assert items == []


def test_recommend_rejects_accessory_in_requested_category():
    session = seed_session(category="笔记本")
    session.products["acc"] = seed_product(
        "acc",
        title="笔记本电脑贴纸 保护膜 配件",
        price_cny=29,
        attributes={"type": "accessory"},
    )
    result = judged(ToolBox(session)).recommend(
        [{"product_id": "acc", "reason": "便宜"}], "ok"
    )
    assert result["ok"] is False
    assert "category" in result["violations"]


def test_recommend_rejects_wrong_category_product():
    session = seed_session(category="笔记本")
    session.products["book"] = seed_product(
        "book", title="三本软件开发书", price_cny=100
    )
    result = judged(ToolBox(session)).recommend(
        [{"product_id": "book", "reason": "测试"}], "ok"
    )
    assert result["ok"] is False
    assert "category" in result["violations"]


def test_query_inventory_excludes_accessory_and_wrong_category():
    session = seed_session(category="笔记本")
    session.products["valid"] = seed_product(
        "valid", title="ThinkPad T14 锐龙7 32G 开发本"
    )
    session.products["acc"] = seed_product(
        "acc", title="笔记本电脑贴纸 保护膜 配件", price_cny=29
    )
    session.products["book"] = seed_product(
        "book", title="三本软件开发书", price_cny=100
    )

    items = judged(ToolBox(session)).query_inventory(limit=10)["items"]

    assert {item["product_id"] for item in items} == {"valid"}


def test_price_range_upper_bound_is_used_for_budget():
    session = seed_session(budget_cny=5000)
    session.products["range"] = seed_product(
        "range",
        price=620,
        currency="USD",
        price_cny=4464,
        price_max_cny=6120,
        price_is_range=True,
    )
    result = judged(ToolBox(session)).recommend(
        [{"product_id": "range", "reason": "区间价"}], "ok"
    )
    assert result["ok"] is False
    assert "budget" in result["violations"]


def test_must_have_rejects_title_negation():
    session = seed_session(must_have=["防水"])
    session.products["p1"] = seed_product("p1", title="不防水背包")
    result = judged(ToolBox(session)).recommend(
        [{"product_id": "p1", "reason": "测试"}], "ok"
    )
    assert result["ok"] is False
    assert "must_have:防水" in result["violations"]


def test_must_have_rejects_negative_attribute_value():
    session = seed_session(must_have=["防水"])
    session.products["p1"] = seed_product(
        "p1", title="通勤背包", attributes={"防水": "否"}
    )
    result = judged(ToolBox(session)).recommend(
        [{"product_id": "p1", "reason": "测试"}], "ok"
    )
    assert result["ok"] is False
    assert "must_have:防水" in result["violations"]


def test_negative_attribute_overrides_positive_title_claim():
    session = seed_session(must_have=["防水"])
    session.products["p1"] = seed_product(
        "p1", title="防水背包", attributes={"防水": "否"}
    )
    result = judged(ToolBox(session)).recommend(
        [{"product_id": "p1", "reason": "测试"}], "ok"
    )
    assert result["ok"] is False
    assert "must_have:防水" in result["violations"]


def test_must_have_accepts_ebay_labeled_ram_attribute():
    session = seed_session(must_have=["32G内存"])
    session.products["p1"] = seed_product(
        "p1",
        title="Lenovo ThinkPad T14",
        attributes={"RAM Size": "32 GB"},
    )
    result = judged(ToolBox(session)).recommend(
        [{"product_id": "p1", "reason": "32G"}], "ok"
    )
    assert result["ok"] is True


def test_listing_does_not_overwrite_verified_detail_price():
    session = seed_session()
    session.products["p1"] = seed_product(
        "p1",
        title="ThinkPad T14 32G",
        price=5200,
        price_cny=5200,
        detail_fetched=True,
        detail={"status": "ok", "title": "ThinkPad T14 32G", "price": 5200},
    )

    ToolBox(session)._upsert_listing(
        "jd",
        {
            "source_product_id": "p1",
            "title": "ThinkPad T14 32G",
            "price": 4800,
            "currency": "CNY",
        },
    )

    product = session.products["p1"]
    assert product["price_cny"] == 5200
    assert product["detail_fetched"] is True


def test_listing_range_price_is_persisted():
    session = seed_session()
    ToolBox(session)._upsert_listing(
        "jd",
        {
            "source_product_id": "p1",
            "title": "ThinkPad T14 32G",
            "price": 620,
            "price_max": 850,
            "price_is_range": True,
            "currency": "USD",
        },
    )

    product = next(iter(session.products.values()))
    assert product["price_is_range"] is True
    assert product["price_cny"] == 4464
    assert product["price_max_cny"] == 6120
