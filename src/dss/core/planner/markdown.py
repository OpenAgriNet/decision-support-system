"""Renders a DiscoveredAnswer as markdown for the model.

Flat key:value bullets for now — no schema-derived field labels yet
(planned as a follow-up). Wrapped in markers so a provider's text is read as
retrieved data, never as instructions, matching moderation's convention.
"""

from __future__ import annotations

from dss.core.planner.markers import RETRIEVED_DATA, wrap_as_data
from dss.core.provider_discovery.models import DiscoveredAnswer


def _render_value(value: object) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def _render_field(key: str, value: object, *, indent: str) -> list[str]:
    if isinstance(value, dict):
        lines = [f"{indent}- {key}:"]
        for nested_key, nested_value in value.items():
            lines.extend(_render_field(nested_key, nested_value, indent=indent + "  "))
        return lines
    return [f"{indent}- {key}: {_render_value(value)}"]


def render_answer_as_markdown(answer: DiscoveredAnswer) -> str:
    """Render a provider's answer for the model, wrapped as data.

    ``wrap_as_data`` rather than bracketing the lines by hand: a provider's
    own field could otherwise contain the end marker and close the block
    early.
    """

    lines: list[str] = []
    for key, value in answer.attributes.items():
        lines.extend(_render_field(key, value, indent=""))
    return wrap_as_data("\n".join(lines), RETRIEVED_DATA)
