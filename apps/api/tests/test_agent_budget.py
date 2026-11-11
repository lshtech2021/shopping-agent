def test_mock_shopper_clarify_questions_include_field():
    from conftest import _requirement_gaps

    gaps = _requirement_gaps(
        {
            "budget_cny": None,
            "budget_unlimited": False,
            "category": None,
            "must_have": [],
        }
    )
    assert {q["id"]: q["field"] for q in gaps} == {
        "budget": "budget",
        "category": "category",
    }


def test_explicit_unlimited_clears_budget():
    from app.store import store
    from app.tools import ToolBox

    session = store.create("笔记本", "jd")
    session.requirements["budget_cny"] = 5000
    result = ToolBox(session).update_requirements(budget_unlimited=True)
    assert result["requirements"]["budget_unlimited"] is True
    assert session.requirements["budget_cny"] is None


def test_low_confidence_budget_is_clarified_via_model_tool(fake_llm):
    """Ambiguous bare numbers stay unset; the model must ask the user."""
    import asyncio

    from conftest import _call
    from app.llm_agent import run_llm_policy
    from app.store import store
    from app.tools import ToolBox

    model = fake_llm(
        [
            _call(
                "ask_user",
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
                reason="clarify",
            )
        ]
    )
    session = store.create("笔记本 5000", "jd")
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
