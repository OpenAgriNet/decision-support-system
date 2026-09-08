"""Loads the planner's fixed instruction template.

The template ships with the DSS and is **not** adopter-configurable, unlike
identity and skills: a bad edit breaks the loop with nothing to catch it, and
the marker rule it carries is a safety property, not a preference. So there is
no ``DSS_*_CONFIG_PATH`` here — it reads only from the package.

It lives in ``config/`` rather than beside ``build_planner_prompt`` because
reading a file is a dependency, and `core/` reaches nothing outside itself
(see ``tests/unit/test_core_isolation.py``). ``core`` validates and fills the
template it is handed; this module is the only thing that knows it is a file.

The template deliberately names no tool and no calling sequence. That is the
skill's job (ADR-0006) — repeating it here would mean a deployment that swaps
the skill still carries the old instructions in its prompt.
"""

from __future__ import annotations

from pathlib import Path

_TEMPLATE = Path(__file__).parent / "defaults" / "planner_prompt.md"


def load_planner_prompt_template() -> str:
    """The shipped template, verbatim. Placeholders are checked where they are
    filled — see ``dss.core.planner.prompt``."""

    return _TEMPLATE.read_text(encoding="utf-8")
