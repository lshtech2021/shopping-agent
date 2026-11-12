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


def test_apply_replaces_existing_must_have_with_the_complete_list():
    req = {"must_have": ["water resistant"]}
    patch = RequirementPatch(must_have=["waterproof", "lightweight"])

    out = apply_requirement_patch(req, patch)

    assert out["must_have"] == ["waterproof", "lightweight"]


def test_apply_preserves_must_have_when_the_patch_omits_it():
    req = {"must_have": ["waterproof"]}
    patch = RequirementPatch(preferences=["lightweight"])

    out = apply_requirement_patch(req, patch)

    assert out["must_have"] == ["waterproof"]
