import asyncio

import pytest

from app.domains.shopping.requirements_patch import RequirementPatch
from app.domains.shopping.tools import build_shopping_tools
from app.store import store
from app.tools import ToolBox


def test_update_requirements_applies_explicit_patch_without_change_cues():
    session = store.create("预算 5000 的笔记本", ["jd"])
    session.requirements["budget_cny"] = 5000

    result = ToolBox(session).update_requirements(budget_cny=8000)

    assert result["requirements"]["budget_cny"] == 8000
    assert session.requirements["budget_cny"] == 8000


def test_domain_update_requirements_ignores_explicit_null_lists():
    session = store.create("找一台笔记本", ["jd"])
    spec = {
        tool.name: tool for tool in build_shopping_tools(ToolBox(session), {})
    }["update_requirements"]

    result = asyncio.run(
        spec.handler(
            spec.args_model(must_have=None, preferences=None),
            None,
        )
    )

    assert result.ok is True
    assert result.data["requirements"]["must_have"] == []
    assert result.data["requirements"]["preferences"] == []


def test_ambiguous_list_constraints_are_not_applied():
    session = store.create("找一台笔记本", ["jd"])
    patch = RequirementPatch(
        must_have=["防水"],
        preferences=["红色"],
        confidence={"must_have": "low", "preferences": "low"},
        ambiguous_fields=["must_have", "preferences"],
    )

    result = ToolBox(session).update_requirements(patch)

    assert result["requirements"]["must_have"] == []
    assert result["requirements"]["preferences"] == []
    assert result["needs_clarify"] is True
