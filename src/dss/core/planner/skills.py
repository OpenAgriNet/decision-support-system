"""Which tools the selected skills bring (ADR-0006).

Tools bind from the union of every selected skill's ``tool_names``, so an
unselected skill's tools are never in the model's schema.
"""

from __future__ import annotations

from collections.abc import Sequence

from dss.core.planner.models import Skill


def tool_names_for(skills: Sequence[Skill]) -> tuple[str, ...]:
    """Every tool the selected skills bring, in first-seen order."""

    names: list[str] = []
    for skill in skills:
        for name in skill.tool_names:
            if name not in names:
                names.append(name)
    return tuple(names)
