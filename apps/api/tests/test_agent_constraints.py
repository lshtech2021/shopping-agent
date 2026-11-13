from app.config import settings
from app.constraints import check_eligibility
from app.store import store
from app.tools import ToolBox
from conftest import judged


def seed_session(**overrides):
    session = store.create("测试需求", overrides.pop("site", "jd"))
    requirements = {
        "budget": None,
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
        "url": f"https://item.jd.com/{product_id}.html",
        "detail_fetched": True,
        "attributes": {},
    }
    product.update(overrides)
    return product


def test_budget_requires_matching_currency():
    product = {
        "source": "ebay",
        "price": 500,
        "currency": "USD",
        "availability": "available",
    }
    requirements = {"budget": 600, "must_have": []}

    matching = check_eligibility(
        product,
        requirements,
        settings={"site": "ebay", "currency": "USD"},
        sites=["ebay"],
    )
    assert "budget" not in matching.violations

    mismatch = check_eligibility(
        product,
        requirements,
        settings={"site": "ebay", "currency": "CNY"},
        sites=["ebay"],
    )
    assert "currency" in mismatch.violations


def test_budget_rejects_missing_session_currency():
    result = check_eligibility(
        {
            "source": "ebay",
            "price": 500,
            "currency": "USD",
            "availability": "available",
        },
        {"budget": 600, "must_have": []},
        settings={"site": "ebay", "currency": None},
        sites=["ebay"],
    )

    assert "currency" in result.violations


def _shipping_product(**overrides):
    product = {
        "source": "ebay",
        "price": 1000,
        "currency": "CNY",
        "shipping_cost": 40,
        "shipping_currency": "CNY",
        "availability": "available",
        "cost_complete": True,
    }
    product.update(overrides)
    return product


def _shipping_settings(**overrides):
    settings_value = {
        "site": "ebay",
        "currency": "CNY",
        "max_shipping_ratio": 0.10,
        "max_shipping_cost": None,
    }
    settings_value.update(overrides)
    return settings_value


def test_shipping_absolute_cap_excludes_even_when_ratio_passes():
    result = check_eligibility(
        _shipping_product(shipping_cost=80),
        {"must_have": []},
        settings=_shipping_settings(max_shipping_cost=50),
        sites=["ebay"],
    )
    assert "shipping" in result.violations


def test_shipping_ratio_cap_excludes_even_when_absolute_passes():
    result = check_eligibility(
        _shipping_product(shipping_cost=90),
        {"must_have": []},
        settings=_shipping_settings(
            max_shipping_ratio=0.05,
            max_shipping_cost=100,
        ),
        sites=["ebay"],
    )
    assert "shipping" in result.violations


def test_shipping_caps_pass_and_free_shipping_is_kept():
    both_pass = check_eligibility(
        _shipping_product(shipping_cost=40),
        {"must_have": []},
        settings=_shipping_settings(max_shipping_cost=50),
        sites=["ebay"],
    )
    free = check_eligibility(
        _shipping_product(shipping_cost=0),
        {"must_have": []},
        settings=_shipping_settings(max_shipping_cost=0),
        sites=["ebay"],
    )
    assert "shipping" not in both_pass.violations
    assert "shipping" not in free.violations


def test_shipping_unknown_is_incomplete_when_caps_are_configured():
    product = _shipping_product(shipping_cost=None, cost_complete=False)
    result = check_eligibility(
        product,
        {"must_have": []},
        settings=_shipping_settings(),
        sites=["ebay"],
    )
    assert "shipping" not in result.violations
    assert "cost_incomplete" in result.violations


def test_shipping_caps_do_not_apply_to_jd():
    product = {
        "source": "jd",
        "price": 5000,
        "currency": "CNY",
        "availability": "available",
        "cost_complete": False,
    }
    result = check_eligibility(
        product,
        {"must_have": []},
        settings={
            "site": "jd",
            "currency": "CNY",
            "max_shipping_ratio": 0.10,
            "max_shipping_cost": None,
        },
        sites=["jd"],
    )
    assert "shipping" not in result.violations
    assert "cost_incomplete" not in result.violations


def test_shipping_currency_mismatch_skips_caps():
    product = _shipping_product(
        shipping_cost=80,
        shipping_currency="USD",
        cost_complete=False,
    )
    result = check_eligibility(
        product,
        {"must_have": []},
        settings=_shipping_settings(max_shipping_cost=10),
        sites=["ebay"],
    )
    assert "shipping" not in result.violations
    assert "cost_incomplete" in result.violations


def test_unlimited_upper_still_enforces_an_explicit_lower_bound():
    below = check_eligibility(
        {
            "source": "jd",
            "price": 4000,
            "currency": "CNY",
            "availability": "available",
            "detail_fetched": True,
        },
        {
            "budget": None,
            "budget_min": 5000,
            "budget_unlimited": True,
            "must_have": [],
        },
        settings={"site": "jd", "currency": "CNY"},
        sites=["jd"],
    )
    above = check_eligibility(
        {
            "source": "jd",
            "price": 6000,
            "currency": "CNY",
            "availability": "available",
            "detail_fetched": True,
        },
        {
            "budget": None,
            "budget_min": 5000,
            "budget_unlimited": True,
            "must_have": [],
        },
        settings={"site": "jd", "currency": "CNY"},
        sites=["jd"],
    )

    assert "budget_min" in below.violations
    assert "budget_min" not in above.violations


def test_budget_uses_landed_cost_when_available():
    product = {
        "source": "ebay",
        "price": 600,
        "landed_cost": 650,
        "currency": "CNY",
        "availability": "available",
        "cost_complete": True,
    }

    too_tight = check_eligibility(
        product,
        {"budget": 620, "must_have": []},
        settings={"site": "ebay", "currency": "CNY"},
        sites=["ebay"],
        require_detail=True,
    )
    assert "budget" in too_tight.violations

    roomy = check_eligibility(
        product,
        {"budget": 700, "must_have": []},
        settings={"site": "ebay", "currency": "CNY"},
        sites=["ebay"],
        require_detail=True,
    )
    assert "budget" not in roomy.violations


def test_auction_and_incomplete_cost_are_ineligible_for_hard_budget():
    product = {
        "source": "ebay",
        "price": 100,
        "currency": "CNY",
        "availability": "available",
        "listing_type": "auction",
        "cost_complete": False,
    }

    result = check_eligibility(
        product,
        {"budget": 1000, "must_have": []},
        settings={"site": "ebay", "currency": "CNY"},
        sites=["ebay"],
        require_detail=True,
    )

    assert "auction" in result.violations
    assert "cost_incomplete" in result.violations


def test_parts_condition_is_ineligible():
    result = check_eligibility(
        {
            "source": "ebay",
            "price": 100,
            "currency": "CNY",
            "availability": "available",
            "condition": "Parts Only",
        },
        {"must_have": []},
        settings={"site": "ebay", "currency": "CNY"},
        sites=["ebay"],
    )
    assert "parts" in result.violations


def test_recommend_rejects_over_budget_product():
    session = seed_session(budget=5000)
    session.products["p1"] = seed_product("p1", price=9000)
    result = judged(ToolBox(session)).recommend(
        [{"product_id": "p1", "reason": "测试"}], "ok"
    )
    assert result["ok"] is False
    assert any(v.startswith("budget") for v in result["violations"])


def test_recommend_rejects_wrong_site_product():
    session = seed_session(sites=["jd"])
    session.products["p1"] = seed_product("p1", source="ebay", price=1000)
    result = judged(ToolBox(session)).recommend(
        [{"product_id": "p1", "reason": "测试"}], "ok"
    )
    assert result["ok"] is False
    assert "site" in result["violations"]


def test_recommend_rejects_missing_generic_must_have():
    session = seed_session(must_have=["防水"])
    session.products["p1"] = seed_product(
        "p1", title="普通背包", attributes={}, price=100
    )
    result = judged(ToolBox(session)).recommend(
        [{"product_id": "p1", "reason": "测试"}], "ok"
    )
    assert result["ok"] is False
    assert any(v.startswith("must_have") for v in result["violations"])


def test_recommend_accepts_must_have_in_attributes():
    session = seed_session(must_have=["防水"])
    session.products["p1"] = seed_product(
        "p1", title="通勤背包", attributes={"防水": "是"}, price=100
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
    session = seed_session(budget=1000, must_have=["防水"])
    session.products["over"] = seed_product("over", price=2000)
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
        price=29,
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
        "book", title="三本软件开发书", price=100
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
        "acc", title="笔记本电脑贴纸 保护膜 配件", price=29
    )
    session.products["book"] = seed_product(
        "book", title="三本软件开发书", price=100
    )

    items = judged(ToolBox(session)).query_inventory(limit=10)["items"]

    assert {item["product_id"] for item in items} == {"valid"}


def test_price_range_upper_bound_is_used_for_budget():
    session = seed_session(budget=700)
    session.settings["currency"] = "USD"
    session.products["range"] = seed_product(
        "range",
        price=620,
        currency="USD",
        price_max=850,
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
    assert product["price"] == 5200
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
    assert product["price"] == 620
    assert product["price_max"] == 850
    assert product["currency"] == "USD"
