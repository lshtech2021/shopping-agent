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
        ("最多 5 个候选", None, None),
        ("一千万像素的相机", None, None),
        ("5000 mAh", None, None),
        ("2024 款 笔记本", None, None),
        ("RTX 4060 笔记本", None, None),
        ("i5-13500H 笔记本", None, None),
        ("512G SSD", None, None),
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
