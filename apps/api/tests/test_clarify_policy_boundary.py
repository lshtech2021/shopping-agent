"""Clarify policy must live in model tools, not host orchestrators."""
from __future__ import annotations

import ast
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1] / "app"


def _source(rel: str) -> str:
    return (APP_ROOT / rel).read_text(encoding="utf-8")


def test_orchestrators_do_not_hardcode_clarify_question_lists():
    for rel in ("agent_runner.py", "llm_agent.py"):
        tree = ast.parse(_source(rel))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            keys = []
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    keys.append(key.value)
            # Host-owned structured clarify payloads look like question dicts.
            if {"id", "question", "options"}.issubset(keys) or {
                "id",
                "field",
                "question",
                "options",
            }.issubset(keys):
                raise AssertionError(
                    f"{rel} still embeds a clarify question dict; "
                    "clarify must be a model tool call"
                )


def test_ask_user_is_the_only_wait_tool_registered_in_shopping_domain():
    from app.domains.shopping.tools import build_shopping_tools
    from app.store import store
    from app.tools import ToolBox

    session = store.create("笔记本", "jd")
    names = {spec.name for spec in build_shopping_tools(ToolBox(session), {})}
    assert "clarify" not in names
    assert "ask_user" in names
    assert "update_requirements" in names
