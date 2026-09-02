"""The read-only barrier: nothing in the discovery slice may invoke a provider.

Discovery runs before moderation clears the turn, so a component holding only
``CapabilityDiscovery`` must not be able to reach ``select``. This is enforced
by type in orchestration/, but ``core`` doesn't get that check for free — this
test walks the AST the same way test_framework_boundary.py does.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[4] / "src"
DISCOVERY_SLICE = SRC / "dss" / "core" / "provider_discovery"


def _imported_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            names.update(alias.name.split(".")[-1] for alias in node.names)
    return names


def _slice_modules() -> list[Path]:
    return sorted(DISCOVERY_SLICE.rglob("*.py"))


def test_discovery_slice_has_modules_to_check() -> None:
    assert DISCOVERY_SLICE.is_dir(), f"expected the slice at {DISCOVERY_SLICE}"
    assert _slice_modules(), "no modules found — this suite would be vacuous"


@pytest.mark.parametrize("module_path", _slice_modules(), ids=lambda p: p.name)
def test_no_discovery_module_imports_capability_invocation(module_path: Path) -> None:
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    assert "CapabilityInvocation" not in _imported_names(tree), (
        f"{module_path.relative_to(SRC)} imports CapabilityInvocation. "
        "Discovery is read-only until the turn clears moderation — "
        "invocation belongs to the Plan Executioner."
    )


def test_the_boundary_check_fails_when_a_violation_is_planted() -> None:
    tree = ast.parse("from dss.ports.invocation import CapabilityInvocation\n")
    assert "CapabilityInvocation" in _imported_names(tree)
