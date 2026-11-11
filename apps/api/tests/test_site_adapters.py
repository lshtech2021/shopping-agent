from __future__ import annotations

import asyncio

import pytest

from app.domains.shopping.adapters import get_adapter
from app.domains.shopping.prompt import SYSTEM_PROMPT, context_block, system_prompt
from app.domains.shopping.tools import build_shopping_tools
from app.store import store
from app.tools import ToolBox


def test_get_adapter_jd_and_ebay():
    assert get_adapter("jd").id == "jd"
    assert get_adapter("ebay").id == "ebay"


def test_get_adapter_rejects_unknown_site():
    with pytest.raises(ValueError, match="adapter_missing"):
        get_adapter("amazon")


def test_ebay_build_search_filters_uses_session_currency_budget():
    adapter = get_adapter("ebay")
    filters = adapter.build_search_filters(
        query="laptop",
        requirements={"budget": 5000},
        settings={"site": "ebay", "currency": "CNY"},
        max_price=None,
    )

    assert filters.get("maxPrice") == 5000


def test_jd_build_search_filters_preserves_plain_keyword_search():
    filters = get_adapter("jd").build_search_filters(
        query="开发笔记本",
        requirements={"budget": 5000},
        settings={"site": "jd", "currency": "CNY"},
        max_price=None,
    )

    assert filters == {}


def test_explicit_max_price_takes_precedence_over_budget():
    filters = get_adapter("ebay").build_search_filters(
        query="laptop",
        requirements={"budget": 5000},
        settings={"site": "ebay", "currency": "CNY"},
        max_price=4500,
    )

    assert filters["maxPrice"] == 4500


def test_adapter_budget_eligibility_requires_matching_currency():
    adapter = get_adapter("jd")
    settings = {"site": "jd", "currency": "CNY"}

    assert adapter.eligible_for_budget(
        {"price": 4999, "currency": "CNY"}, settings, 5000
    )
    assert not adapter.eligible_for_budget(
        {"price": 4999, "currency": "USD"}, settings, 5000
    )
    assert not adapter.eligible_for_budget(
        {"price": 5001, "currency": "CNY"}, settings, 5000
    )


def test_ebay_prompt_extra_has_no_fx_instruction():
    text = get_adapter("ebay").system_prompt_extra(
        {"site": "ebay", "currency": "USD"},
        {"budget": 500},
    )

    assert "convert" not in text.lower()
    assert "CNY" not in text or "budget is in USD" in text.lower() or "USD" in text


def test_shared_prompt_is_site_neutral_and_pack_extra_is_composed():
    assert "eBay" not in SYSTEM_PROMPT
    assert "Convert a CNY budget" not in SYSTEM_PROMPT
    assert "Active site and currency come from session settings" in SYSTEM_PROMPT

    composed = system_prompt(
        {"site": "ebay", "currency": "USD"}, {"budget": 500}
    )
    assert "eBay" in composed
    assert "USD" in composed


def test_context_block_shows_singular_site_and_currency():
    session = store.create("laptop", "ebay")
    session.settings["currency"] = "USD"

    text = context_block(ToolBox(session))

    assert "站点：ebay" in text
    assert "币种：USD" in text
    assert "['ebay']" not in text


def test_search_uses_session_site_and_adapter_owned_budget(monkeypatch):
    session = store.create("laptop", "ebay")
    session.settings["currency"] = "USD"
    session.requirements["budget"] = 500
    box = ToolBox(session)
    search = next(
        spec for spec in build_shopping_tools(box, {}) if spec.name == "search"
    )
    seen: dict[str, object] = {}

    async def fake_search(site, query, **filters):
        seen.update(site=site, query=query, filters=filters)
        return {"status": "ok", "site": site}

    monkeypatch.setattr(box, "search", fake_search)
    args = search.args_model.model_validate(
        {"site": "jd", "query": "laptop", "max_price": None}
    )

    asyncio.run(search.handler(args, None))

    assert seen == {
        "site": "ebay",
        "query": "laptop",
        "filters": {"maxPrice": 500},
    }
