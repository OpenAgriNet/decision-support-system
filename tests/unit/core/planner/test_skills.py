"""Tier 1 — which tools a set of selected skills brings (ADR-0006)."""

from __future__ import annotations

from dss.core.planner.models import Skill
from dss.core.planner.skills import tool_names_for


def _skill(skill_id: str, tool_names: tuple[str, ...]) -> Skill:
    return Skill(
        id=skill_id,
        domain="agriculture",
        description=f"the {skill_id} skill",
        guidance="do the thing",
        tool_names=tool_names,
    )


def test_the_tools_are_the_union_of_every_selected_skill() -> None:
    skills = [
        _skill("provider-invocation", ("describe_capability", "select")),
        _skill("weather-advisory", ("forecast",)),
    ]

    assert tool_names_for(skills) == ("describe_capability", "select", "forecast")


def test_a_tool_two_skills_share_is_named_once() -> None:
    """Registering the same tool twice is an error in Pydantic AI, so the
    union has to dedupe rather than concatenate."""

    skills = [
        _skill("provider-invocation", ("describe_capability", "select")),
        _skill("weather-advisory", ("select", "forecast")),
    ]

    assert tool_names_for(skills) == ("describe_capability", "select", "forecast")
