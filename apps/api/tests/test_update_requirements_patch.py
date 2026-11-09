import asyncio

from app.domains.shopping.tools import build_shopping_tools
from app.store import store
from app.tools import ToolBox


def test_update_requirements_applies_explicit_patch_without_change_cues():
    session = store.create("预算 5000 的笔记本", ["jd"])
    session.requirements["budget_cny"] = 5000

    result = ToolBox(session).update_requirements("", budget_cny=8000)

    assert result["requirements"]["budget_cny"] == 8000
    assert session.requirements["budget_cny"] == 8000


def test_domain_update_requirements_extracts_free_text():
    session = store.create("找一台笔记本", ["jd"])
    spec = {
        tool.name: tool for tool in build_shopping_tools(ToolBox(session), {})
    }["update_requirements"]

    result = asyncio.run(spec.handler(spec.args_model(text="预算 6000 元"), None))

    assert result.data["requirements"]["budget_cny"] == 6000
