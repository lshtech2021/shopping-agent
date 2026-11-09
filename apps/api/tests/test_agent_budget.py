import pytest

from app.tools import parse_budget


@pytest.mark.parametrize(
    "text,max_cny,min_cny",
    [
        ("预算5000", 5000, None),
        ("预算3000-5000", 5000, None),
        ("预算 1.5万", 15000, None),
        ("5000元以内", 5000, None),
        ("4000-6000元", 6000, None),
        ("八千左右", 8000, None),
        ("预算五千", 5000, None),
        ("1.5万以上", None, 15000),
        ("8000以上", None, 8000),
        ("至少8000元", None, 8000),
        ("不低于8000元", None, 8000),
        ("8000元以上", None, 8000),
        ("8000元起", None, 8000),
        ("预算8000元以上", None, 8000),
        ("1.5万元以上", None, 15000),
        ("1万5", 15000, None),
        ("预算五千到八千", 8000, None),
        ("价格不要超过8000", 8000, None),
        ("8000以内", 8000, None),
        ("品牌无所谓，预算8000", 8000, None),
        ("预算8000，颜色都可以", 8000, None),
        ("最多 5 个候选", None, None),
        ("一千万像素的相机", None, None),
        ("5000 mAh", None, None),
        ("2024 款 笔记本", None, None),
        ("RTX 4060 笔记本", None, None),
        ("i5-13500H 笔记本", None, None),
        ("512G SSD", None, None),
        ("4K显示器", None, None),
        ("2K屏笔记本", None, None),
        ("6000K色温灯", None, None),
    ],
)
def test_parse_budget(text, max_cny, min_cny):
    parsed = parse_budget(text)
    assert parsed.max_cny == max_cny
    assert parsed.min_cny == min_cny


def test_low_confidence_bare_number_is_not_written(monkeypatch):
    from app.store import store
    from app.tools import ToolBox

    session = store.create("笔记本 5000", ["jd"])
    result = ToolBox(session).update_requirements("笔记本 5000")
    assert result["needs_clarify"] is True
    assert session.requirements["budget_cny"] is None


def test_explicit_unlimited_clears_budget():
    from app.store import store
    from app.tools import ToolBox

    session = store.create("笔记本", ["jd"])
    session.requirements["budget_cny"] = 5000
    result = ToolBox(session).update_requirements("预算不限")
    assert result["requirements"]["budget_unlimited"] is True
    assert session.requirements["budget_cny"] is None


def test_budget_unlimited_requires_budget_context():
    parsed = parse_budget("品牌无所谓，预算8000")
    assert parsed.max_cny == 8000
    assert parsed.min_cny is None


def test_low_confidence_budget_is_clarified_via_model_tool(fake_llm):
    """Ambiguous bare numbers stay unset; the model must call clarify."""
    import asyncio

    from conftest import _call
    from app.llm_agent import run_llm_policy
    from app.store import store
    from app.tools import ToolBox

    model = fake_llm(
        [
            _call(
                "clarify",
                questions=[
                    {
                        "id": "budget",
                        "field": "budget",
                        "question": "预算大概是多少？请给出一个上限，或选择「不限预算」。",
                        "options": [
                            "3000以内",
                            "3000-5000",
                            "5000-8000",
                            "8000-15000",
                            "1.5万以上",
                            "不限预算",
                        ],
                    }
                ],
            )
        ]
    )
    session = store.create("笔记本 5000", ["jd"])
    session.requirements = {
        "budget_cny": None,
        "budget_min_cny": None,
        "budget_unlimited": False,
        "must_have": [],
        "preferences": [],
        "category": None,
        "use_case": None,
    }

    result = asyncio.run(
        run_llm_policy(session, "笔记本 5000", ToolBox(session))
    )

    assert result["interrupted"] is True
    assert result["interrupt_reason"] == "clarify"
    assert result["clarify"][0]["field"] == "budget"
    assert model.calls == 1
    assert session.requirements["budget_cny"] is None
