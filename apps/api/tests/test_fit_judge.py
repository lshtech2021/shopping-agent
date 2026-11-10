import asyncio

from app import fit_judge
from app.constraints import check_product, needs_judging
from app.domains.shopping.tools import build_shopping_tools
from app.store import store
from app.tools import ToolBox
from conftest import FitJudgeDouble


def seed(**requirements):
    session = store.create("测试", ["jd"])
    session.requirements = {
        "budget_cny": None,
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
        "price_cny": 100.0,
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
        session.products["ok"], session.requirements, sites=["jd"]
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
    ok = check_product(session.products["ok"], session.requirements, sites=["jd"])
    acc = check_product(session.products["acc"], session.requirements, sites=["jd"])
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
    result = check_product(session.products["ok"], session.requirements, sites=["jd"])
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
