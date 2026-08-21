"""The framework boundary: only ``dss.orchestration`` may import the framework.

This is a hard boundary — ``dss.core`` stays framework-agnostic so it is
unit-testable without a framework runtime and a framework swap stays confined to
``dss.orchestration``.

ruff's TID251 ban in ``pyproject.toml`` is the first line of defence, but ruff's
own documentation notes it "is only meant to flag accidental uses, and can be
circumvented via ``eval`` or ``importlib``". These tests walk the AST instead, so
the rule holds against a dynamic import that ruff cannot see.

``pydantic_graph`` is checked alongside ``pydantic_ai`` deliberately:
``pydantic-ai-slim`` pulls ``pydantic-graph`` in as a *core* dependency under a
separate top-level import name, so checking only ``pydantic_ai`` would leave
``core/`` free to import the graph library.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

BANNED_ROOT_MODULES = frozenset({"pydantic_ai", "pydantic_graph"})

SRC = Path(__file__).resolve().parents[2] / "src"
CORE = SRC / "dss" / "core"


def _imported_root_modules(tree: ast.AST) -> set[str]:
    """``import a.b`` and ``from a.b import c`` both yield ``"a"``.

    Relative imports yield nothing.
    """
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def _dynamic_import_names(tree: ast.AST) -> set[str]:
    """Module names passed as string literals to ``import_module``/``__import__``.

    A computed module name is undetectable; no static check can close that gap.
    """
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        is_import_call = (
            isinstance(target, ast.Name) and target.id == "__import__"
        ) or (isinstance(target, ast.Attribute) and target.attr == "import_module")
        if not is_import_call:
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                found.add(arg.value.split(".")[0])
    return found


def _core_modules() -> list[Path]:
    return sorted(CORE.rglob("*.py"))


def test_core_package_has_modules_to_check() -> None:
    assert CORE.is_dir(), f"expected the core package at {CORE}"
    assert _core_modules(), (
        "no modules found under dss.core — this suite would be vacuous"
    )


@pytest.mark.parametrize("module_path", _core_modules(), ids=lambda p: p.name)
def test_no_core_module_imports_the_framework(module_path: Path) -> None:
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    offending = (
        _imported_root_modules(tree) | _dynamic_import_names(tree)
    ) & BANNED_ROOT_MODULES
    assert not offending, (
        f"{module_path.relative_to(SRC)} imports {sorted(offending)}. "
        "Only dss.orchestration may import the agentic framework — core must "
        "depend on dss.ports instead."
    )


def test_a_plain_framework_import_is_caught() -> None:
    tree = ast.parse("import pydantic_ai\n")
    assert _imported_root_modules(tree) & BANNED_ROOT_MODULES == {"pydantic_ai"}


def test_a_from_import_of_the_graph_library_is_caught() -> None:
    tree = ast.parse("from pydantic_graph import Graph\n")
    assert _imported_root_modules(tree) & BANNED_ROOT_MODULES == {"pydantic_graph"}


def test_a_framework_import_hidden_behind_importlib_is_caught() -> None:
    tree = ast.parse("import importlib\nm = importlib.import_module('pydantic_ai')\n")
    assert _dynamic_import_names(tree) & BANNED_ROOT_MODULES == {"pydantic_ai"}


def test_the_boundary_check_fails_when_a_violation_is_planted() -> None:
    tree = ast.parse("from pydantic_ai import Agent\nimport pydantic\n")
    offending = (
        _imported_root_modules(tree) | _dynamic_import_names(tree)
    ) & BANNED_ROOT_MODULES
    assert offending == {"pydantic_ai"}


def test_imports_core_is_allowed_to_make_are_not_flagged() -> None:
    source = (
        "from __future__ import annotations\n"
        "import pydantic\n"
        "from dss.ports import LLMProvider\n"
        "from . import models\n"
        "from .models import Intent\n"
    )
    tree = ast.parse(source)
    assert not (_imported_root_modules(tree) & BANNED_ROOT_MODULES)
