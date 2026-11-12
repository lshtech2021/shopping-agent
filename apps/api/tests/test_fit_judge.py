import asyncio

from app import fit_judge
from app.constraints import check_product, needs_judging
from app.domains.shopping.tools import build_shopping_tools
from app.store import store
from app.tools import ToolBox
from conftest import FitJudgeDouble


def seed(**requirements):
    session = store.create("测试", "jd")
    session.requirements = {
        "budget": None,
        "must_have": [],
        "category": None,
        **requirements,
    }
    session.products["ok"] = _product("ok", "ThinkPad T14 笔记本 32G 1T")
    session.products["acc"] = _product("acc", "笔记本贴膜 配件")
    return session


def _product(product_id, title):
    return {
        "product_id": product_id,
        "source": "jd",
        "title": title,
        "detail_fetched": True,
        "attributes": {},
    }


class CountingJudge(FitJudgeDouble):
    def __init__(self):
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        return await super().ainvoke(messages)


class BrokenJudge:
    def with_structured_output(self, _schema):
        return self

    async def ainvoke(self, _messages):
        raise RuntimeError("boom")


def test_no_category_or_must_have_means_no_llm_call():
    session = seed()
    judge = CountingJudge()

    outcome = asyncio.run(
        fit_judge.judge_products(
            list(session.products.values()), session.requirements, model=judge
        )
    )

    assert judge.calls == 0 and outcome == {"judged": 0, "error": None}
    assert check_product(
        session.products["ok"],
        session.requirements,
        settings=session.settings,
        sites=["jd"],
    ).ok


def test_verdicts_are_batched_cached_and_gate_eligibility():
    session = seed(category="笔记本", must_have=["32G内存"])
    judge = CountingJudge()
    products = list(session.products.values())

    first = asyncio.run(
        fit_judge.judge_products(products, session.requirements, model=judge)
    )
    again = asyncio.run(
        fit_judge.judge_products(products, session.requirements, model=judge)
    )

    assert first["judged"] == 2 and again["judged"] == 0
    assert judge.calls == 1  # one batched call, then cache hit
    ok = check_product(
        session.products["ok"], session.requirements, settings=session.settings, sites=["jd"]
    )
    acc = check_product(
        session.products["acc"], session.requirements, settings=session.settings, sites=["jd"]
    )
    assert ok.ok
    assert "category" in acc.violations


def test_verdict_goes_stale_when_requirements_or_detail_change():
    session = seed(category="笔记本")
    product = session.products["ok"]
    asyncio.run(
        fit_judge.judge_products([product], session.requirements, model=CountingJudge())
    )
    assert not needs_judging(product, session.requirements)

    assert needs_judging(product, {**session.requirements, "must_have": ["防水"]})
    product["detail_fetched"] = False
    assert needs_judging(product, session.requirements)


def test_judge_failure_leaves_products_unjudged_and_ineligible():
    session = seed(category="笔记本")

    outcome = asyncio.run(
        fit_judge.judge_products(
            list(session.products.values()),
            session.requirements,
            model=BrokenJudge(),
        )
    )

    assert outcome["judged"] == 0 and "boom" in outcome["error"]
    result = check_product(
        session.products["ok"], session.requirements, settings=session.settings, sites=["jd"]
    )
    assert result.violations == ["unjudged"]


def test_query_inventory_tool_judges_and_reports_judge_errors(monkeypatch):
    session = seed(category="笔记本")
    box = ToolBox(session)
    spec = {t.name: t for t in build_shopping_tools(box, {})}["query_inventory"]

    result = asyncio.run(spec.handler(spec.args_model(limit=10), None))
    assert [item["product_id"] for item in result.data["items"]] == ["ok"]
    assert "judge_error" not in result.data

    session.requirements = {**session.requirements, "must_have": ["防水"]}
    monkeypatch.setattr(fit_judge, "build_judge_model", lambda: BrokenJudge())
    result = asyncio.run(spec.handler(spec.args_model(limit=10), None))
    assert result.data["items"] == []
    assert "boom" in result.data["judge_error"]


class StructuredUnavailableJudge:
    """Mimics providers that reject response_format but accept plain JSON."""

    def __init__(self):
        self.structured_calls = 0
        self.text_calls = 0

    def with_structured_output(self, _schema):
        outer = self

        class BrokenStructured:
            async def ainvoke(self, _messages):
                outer.structured_calls += 1
                raise RuntimeError(
                    "Error code: 400 - {'error': {'message': "
                    "'This response_format type is unavailable now', "
                    "'type': 'invalid_request_error'}}"
                )

        return BrokenStructured()

    async def ainvoke(self, messages):
        self.text_calls += 1
        return await FitJudgeDouble().ainvoke(messages)


def test_structured_output_unavailable_falls_back_to_json_text():
    session = seed(category="笔记本")
    judge = StructuredUnavailableJudge()

    outcome = asyncio.run(
        fit_judge.judge_products(
            list(session.products.values()), session.requirements, model=judge
        )
    )

    assert outcome["judged"] == 2 and outcome["error"] is None
    assert judge.structured_calls >= 1 and judge.text_calls >= 1
    assert check_product(
        session.products["ok"],
        session.requirements,
        settings=session.settings,
        sites=["jd"],
    ).ok


def test_structured_output_failure_is_cached_for_default_model(monkeypatch):
    session = seed(category="笔记本")
    judge = StructuredUnavailableJudge()
    monkeypatch.setattr(fit_judge, "build_judge_model", lambda: judge)

    asyncio.run(
        fit_judge.judge_products(
            list(session.products.values()), session.requirements
        )
    )
    session.products["ok2"] = _product("ok2", "ThinkPad T14 笔记本 32G")
    asyncio.run(
        fit_judge.judge_products(
            [session.products["ok2"]], session.requirements
        )
    )

    assert judge.structured_calls == 1
    assert judge.text_calls >= 2


def test_structured_output_failure_is_cached_per_model():
    session = seed(category="笔记本")
    judge = StructuredUnavailableJudge()

    asyncio.run(
        fit_judge.judge_products(
            list(session.products.values()), session.requirements, model=judge
        )
    )
    session.products["ok2"] = _product("ok2", "ThinkPad T14 笔记本 32G")
    session.products["acc2"] = _product("acc2", "笔记本贴膜 配件")
    asyncio.run(
        fit_judge.judge_products(
            [session.products["ok2"], session.products["acc2"]],
            session.requirements,
            model=judge,
        )
    )

    assert judge.structured_calls == 1
    assert judge.text_calls >= 2


def test_ensure_fit_skips_products_rejected_by_deterministic_constraints():
    session = store.create("generic", "ebay")
    session.settings.update(
        {
            "currency": "CNY",
            "max_shipping_ratio": 0.1,
            "max_shipping_cost": None,
        }
    )
    session.requirements = {
        "category": "widget",
        "must_have": ["waterproof"],
        "budget": 1000,
        "budget_currency": "CNY",
    }
    for index in range(60):
        shipping = 5 if index < 7 else 50
        session.products[f"p{index}"] = {
            "product_id": f"p{index}",
            "source": "ebay",
            "title": f"widget waterproof {index}",
            "price": 100,
            "currency": "CNY",
            "shipping_cost": shipping,
            "shipping_currency": "CNY",
            "cost_complete": True,
            "detail_fetched": True,
            "attributes": {},
        }

    box = ToolBox(session)
    asyncio.run(box.ensure_fit())

    judged = [
        product
        for product in session.products.values()
        if isinstance(product.get("fit"), dict)
    ]
    assert len(judged) == 7


def test_ensure_fit_limits_the_judged_candidate_set():
    session = seed(category="笔记本")
    for index in range(70):
        session.products[f"p{index}"] = _product(
            f"p{index}", f"ThinkPad T14 笔记本 {index}"
        )
    box = ToolBox(session)

    asyncio.run(box.ensure_fit(limit=10))

    judged = [
        product
        for product in session.products.values()
        if isinstance(product.get("fit"), dict)
    ]
    assert len(judged) == 10


def test_structured_output_fallback_reports_provider_event():
    session = seed(category="笔记本")
    judge = StructuredUnavailableJudge()
    events = []

    def on_event(stage, data):
        events.append((stage, data))

    asyncio.run(
        fit_judge.judge_products(
            list(session.products.values()),
            session.requirements,
            model=judge,
            on_event=on_event,
        )
    )

    assert events
    stage, data = events[0]
    assert stage == "provider.fallback"
    assert data["component"] == "fit_judge"
    assert data["error_code"] == "provider_structured_output_unsupported"
    call_events = [item for item in events if item[0] == "llm.call"]
    assert call_events
    assert call_events[0][1]["component"] == "fit_judge"
    assert call_events[0][1]["duration_ms"] >= 0
