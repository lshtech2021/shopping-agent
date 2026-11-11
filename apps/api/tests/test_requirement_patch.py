from app.domains.shopping.requirements_patch import (
    RequirementPatch,
    apply_requirement_patch,
)


def test_apply_sets_budget_and_category():
    req = {"budget": None, "category": None, "must_have": [], "preferences": []}
    patch = RequirementPatch(
        budget=5000,
        category="笔记本",
        confidence={"budget": "high", "category": "high"},
    )
    out = apply_requirement_patch(req, patch)
    assert out["budget"] == 5000
    assert "budget_cny" not in out
    assert out["category"] == "笔记本"
    assert out.get("budget_unlimited") is False


def test_apply_skips_low_confidence_budget():
    req = {"budget": None}
    patch = RequirementPatch(
        budget=5000,
        confidence={"budget": "low"},
        ambiguous_fields=["budget"],
    )
    out = apply_requirement_patch(req, patch)
    assert out.get("budget") is None


def test_apply_replaces_existing_ram_requirement():
    req = {"must_have": ["32G内存"]}
    patch = RequirementPatch(must_have=["16G内存"])

    out = apply_requirement_patch(req, patch)

    assert out["must_have"] == ["16G内存"]
