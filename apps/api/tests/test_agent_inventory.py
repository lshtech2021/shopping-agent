from app.store import store
from app.tools import ToolBox


def seed_session(**overrides):
    session = store.create("测试需求", overrides.pop("site", "jd"))
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


def test_query_inventory_does_not_hide_high_quality_product():
    session = seed_session(budget_cny=5000)
    for index in range(11):
        session.products[f"p{index}"] = seed_product(
            f"p{index}",
            title=f"低配笔记本 {index} 4G 机械硬盘",
            price_cny=500 + index * 100,
        )
    session.products["good"] = seed_product(
        "good",
        title="ThinkPad T490 i7 32G 1T SSD 高配",
        price_cny=4800,
    )

    items = ToolBox(session).query_inventory(limit=10)["items"]
    assert any("T490" in item["title"] for item in items)


def test_query_inventory_excludes_over_budget():
    session = seed_session(budget_cny=5000)
    session.products["p1"] = seed_product("p1", price_cny=9000)
    assert ToolBox(session).query_inventory(limit=10)["items"] == []


def test_query_inventory_returns_observation_fields():
    session = seed_session(budget_cny=5000)
    session.products["p1"] = seed_product(
        "p1",
        title="ThinkPad T490 32G",
        price_cny=3000,
        shop_name="自营店",
        sales=120,
        rating=4.8,
        attributes={"内存": "32GB"},
        price_max_cny=4200,
        price_is_range=True,
        shipping="免运费",
        condition="二手",
        sponsored=False,
        category="笔记本",
    )

    item = ToolBox(session).query_inventory(limit=10)["items"][0]
    assert item["shop_name"] == "自营店"
    assert item["sales"] == 120
    assert item["rating"] == 4.8
    assert item["attributes"] == {"内存": "32GB"}
    assert item["detail_fetched"] is True
    assert item["price_max_cny"] == 4200
    assert item["price_is_range"] is True
    assert item["shipping"] == "免运费"
    assert item["condition"] == "二手"
    assert item["category"] == "笔记本"


def test_browse_observation_keeps_pagination_signals():
    from app.domains.shopping.tools import _compact_browse

    result = _compact_browse(
        {
            "status": "ok",
            "items": [{"title": "x"}],
            "result_count": 40,
            "has_next": True,
            "page": 1,
        }
    )

    assert result["result_count"] == 40
    assert result["has_next"] is True


def test_query_inventory_reports_diverse_mode():
    session = seed_session()
    session.products["p1"] = seed_product("p1")

    result = ToolBox(session).query_inventory(limit=10)
    assert result["sort"] == "diverse"
    assert result["count"] == 1
    assert result["total_cached"] == 1
    assert result["excluded"] == {}
    assert result["excluded_samples"] == []


def test_query_inventory_reports_exclusion_reasons():
    session = seed_session(budget_cny=500)
    session.products["p1"] = seed_product("p1", price_cny=900)

    result = ToolBox(session).query_inventory(limit=10)

    assert result["count"] == 0
    assert result["total_cached"] == 1
    assert result["excluded"]["budget"] == 1
    assert result["excluded_samples"] == [
        {
            "product_id": "p1",
            "title": "商品",
            "violations": ["budget"],
        }
    ]
