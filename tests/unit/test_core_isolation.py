"""The inward rule: `core/` and `ports/` depend on nothing outward.

`test_framework_boundary.py` bans the agent framework from `core/`.
`test_transport_boundary.py` stops the transport reaching past the port. Neither
checks the rule the whole structure rests on — that the inside of the hexagon
imports nothing from the outside.

Without this, `from dss.adapters.llm.stub import StubLLM` inside a core service
passes every check in the suite, and the claim "swapping the framework rewrites
only `orchestration/`" quietly stops being true.

Two rules, both enforced by walking the AST:

1. **No outward package.** `core/` and `ports/` may not import `adapters`,
   `orchestration`, `entrypoint`, or `config`.
2. **Nothing that reaches the outside world.** No HTTP client, no filesystem, no
   clock, no environment, no randomness. Each of those is a dependency, and a
   dependency belongs behind an argument or a port — otherwise the domain cannot
   be tested without infrastructure, which is the only reason to pay for this
   layout at all.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from tests.support.imports import dynamic_import_modules, imported_modules

SRC = Path(__file__).resolve().parents[2] / "src"
INSIDE = (SRC / "dss" / "core", SRC / "dss" / "ports")

OUTWARD_PACKAGES = (
    "dss.adapters",
    "dss.orchestration",
    "dss.entrypoint",
    "dss.config",
)

# `pydantic` is allowed: it validates and makes no network call. The test for
# "may this library be in core?" is whether importing it drags in I/O, a clock,
# or a vendor account.
IMPURE_MODULES = frozenset(
    {
        "httpx",
        "httpx2",
        "requests",
        "aiohttp",
        "urllib",
        "urllib3",
        "socket",
        "openai",
        "anthropic",
        "boto3",
        "os",
        "pathlib",
        "shutil",
        "tempfile",
        "subprocess",
        "random",
        "secrets",
        "time",
        "sqlite3",
        "logging",
    }
)


def _modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return imported_modules(tree) | dynamic_import_modules(tree)


def _inside_modules() -> list[Path]:
    return sorted(p for root in INSIDE for p in root.rglob("*.py"))


def _outward(modules: set[str]) -> set[str]:
    return {m for m in modules if m.startswith(OUTWARD_PACKAGES)}


def _impure(modules: set[str]) -> set[str]:
    return {m for m in modules if m.split(".")[0] in IMPURE_MODULES}


def test_there_are_modules_inside_the_hexagon_to_check() -> None:
    assert _inside_modules(), "nothing under core/ or ports/ — this suite is vacuous"


@pytest.mark.parametrize("module_path", _inside_modules(), ids=lambda p: p.name)
def test_nothing_inside_imports_an_outer_package(module_path: Path) -> None:
    offending = _outward(_modules(module_path))

    assert not offending, (
        f"{module_path.relative_to(SRC)} imports {sorted(offending)}. The inside "
        "of the hexagon depends on nothing outward — adapters implement ports, "
        "not the other way round."
    )


@pytest.mark.parametrize("module_path", _inside_modules(), ids=lambda p: p.name)
def test_nothing_inside_reaches_the_outside_world(module_path: Path) -> None:
    offending = _impure(_modules(module_path))

    assert not offending, (
        f"{module_path.relative_to(SRC)} imports {sorted(offending)}. Anything "
        "nondeterministic is a dependency — clock, filesystem, environment, "
        "randomness, network. Pass it in as an argument or put it behind a port."
    )


def test_an_outward_import_is_caught() -> None:
    tree = ast.parse("from dss.adapters.llm.stub import StubLLM\n")

    assert _outward(imported_modules(tree)) == {"dss.adapters.llm.stub"}


def test_an_outward_import_hidden_behind_importlib_is_caught() -> None:
    source = "import importlib\nm = importlib.import_module('dss.orchestration')\n"

    assert _outward(dynamic_import_modules(ast.parse(source))) == {"dss.orchestration"}


def test_a_clock_or_a_network_client_is_caught() -> None:
    tree = ast.parse("import time\nimport httpx\n")

    assert _impure(imported_modules(tree)) == {"time", "httpx"}


def test_what_the_inside_is_allowed_to_import_is_not_flagged() -> None:
    source = (
        "from __future__ import annotations\n"
        "from enum import StrEnum\n"
        "from typing import Protocol\n"
        "import pydantic\n"
        "from pydantic import BaseModel\n"
        "from dss.core.shared.models import UserTurn\n"
        "from dss.ports.turn import TurnRunner\n"
    )
    modules = imported_modules(ast.parse(source))

    assert not _outward(modules)
    assert not _impure(modules)
