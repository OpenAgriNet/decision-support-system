"""The transport boundary: the HTTP layer may not reach past the port.

`test_framework_boundary.py` enforces the inward rule — `core/` imports no
framework. This enforces the other direction, which nothing checked before: the
driving adapter must go through `dss.ports`, not call a core service itself.

Break it and the hexagon's whole claim goes with it. A transport that calls
`recognise_intent` directly means the orchestrator is no longer the only thing
that decides the order, and no test of the port proves anything about the real
request path.

`core.shared` is exempt because it is the domain *language* — the types the
request maps into. Depending on a type is not skipping a hop; calling a service
is.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.support.imports import dynamic_import_modules, imported_modules

SRC = Path(__file__).resolve().parents[2] / "src"
TRANSPORT_ROOTS = (SRC / "dss" / "adapters" / "http", SRC / "dss" / "entrypoint")

ALLOWED_CORE_PREFIXES = ("dss.core.shared",)

# The composition root is the one module allowed to name every concrete type,
# core ones included — building a runner means loading a policy pack, which
# needs `dss.core.policy`. Exempting it by name keeps the rule meaningful for
# every other transport module.
COMPOSITION_ROOT = "composition.py"


def _forbidden(modules: set[str]) -> set[str]:
    return {
        name
        for name in modules
        if name.startswith("dss.core") and not name.startswith(ALLOWED_CORE_PREFIXES)
    }


def _transport_modules() -> list[Path]:
    return sorted(
        p
        for root in TRANSPORT_ROOTS
        for p in root.rglob("*.py")
        if p.name != COMPOSITION_ROOT
    )


def test_there_are_transport_modules_to_check() -> None:
    assert _transport_modules(), (
        "no modules found under adapters/http or entrypoint — this suite would "
        "be vacuous"
    )


@pytest.mark.parametrize("module_path", _transport_modules(), ids=lambda p: p.name)
def test_no_transport_module_calls_into_a_core_service(module_path: Path) -> None:
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    offending = _forbidden(imported_modules(tree) | dynamic_import_modules(tree))
    assert not offending, (
        f"{module_path.relative_to(SRC)} imports {sorted(offending)}. The "
        "transport must go through dss.ports — only dss.core.shared (the domain "
        "language) may be imported directly."
    )


def test_importing_a_core_service_is_caught() -> None:
    tree = ast.parse("from dss.core.intent.service import recognise_intent\n")

    assert _forbidden(imported_modules(tree)) == {"dss.core.intent.service"}


def test_a_core_service_hidden_behind_importlib_is_caught() -> None:
    source = "import importlib\nm = importlib.import_module('dss.core.intent')\n"
    tree = ast.parse(source)

    assert _forbidden(dynamic_import_modules(tree)) == {"dss.core.intent"}


def test_the_domain_language_and_the_ports_are_allowed() -> None:
    source = (
        "from dss.core.shared.models import UserTurn\n"
        "from dss.core.shared.errors import ProviderUnavailable\n"
        "from dss.ports.turn import TurnRunner\n"
        "import pydantic\n"
    )

    assert not _forbidden(imported_modules(ast.parse(source)))
