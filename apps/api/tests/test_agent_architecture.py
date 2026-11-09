"""Generic agent package must stay free of shopping-domain coupling."""
from __future__ import annotations

import ast
from pathlib import Path

AGENT_ROOT = Path(__file__).resolve().parents[1] / "app" / "agent"

# Business identifiers that must not appear as names in the generic runtime.
FORBIDDEN_NAMES = frozenset(
    {
        "shopping",
        "requirements",
        "inventory",
        "recommend",
        "clarify",
        "query_inventory",
        "fetch_detail",
        "get_session",
    }
)


def _agent_py_files() -> list[Path]:
    return sorted(AGENT_ROOT.rglob("*.py"))


def test_agent_package_does_not_import_domains():
    offenders: list[str] = []
    for path in _agent_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module == "app.domains" or node.module.startswith(
                    "app.domains."
                ):
                    offenders.append(f"{path.name}:{node.lineno} import {node.module}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "app.domains" or alias.name.startswith(
                        "app.domains."
                    ):
                        offenders.append(
                            f"{path.name}:{node.lineno} import {alias.name}"
                        )
    assert offenders == []


def test_agent_package_has_no_shopping_business_names():
    offenders: list[str] = []
    for path in _agent_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
                offenders.append(f"{path.name}:{node.lineno} Name {node.id}")
            if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
                offenders.append(f"{path.name}:{node.lineno} attr .{node.attr}")
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value in FORBIDDEN_NAMES:
                    offenders.append(
                        f"{path.name}:{node.lineno} str {node.value!r}"
                    )
    assert offenders == []
