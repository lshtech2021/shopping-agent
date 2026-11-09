from app.domains.shopping.requirements_patch import (
    RequirementPatch,
    apply_requirement_patch,
)


def test_apply_sets_budget_and_category():
    req = {"budget_cny": None, "category": None, "must_have": [], "preferences": []}
    patch = RequirementPatch(
        budget_cny=5000,
        category="笔记本",
        confidence={"budget_cny": "high", "category": "high"},
    )
    out = apply_requirement_patch(req, patch)
    assert out["budget_cny"] == 5000
    assert out["category"] == "笔记本"
    assert out.get("budget_unlimited") is False


def test_apply_skips_low_confidence_budget():
    req = {"budget_cny": None}
    patch = RequirementPatch(
        budget_cny=5000,
        confidence={"budget_cny": "low"},
        ambiguous_fields=["budget_cny"],
    )
    out = apply_requirement_patch(req, patch)
    assert out.get("budget_cny") is None
