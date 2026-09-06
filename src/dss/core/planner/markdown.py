"""Renders a DiscoveredAnswer as markdown for the model.

Flat key:value bullets for now — no schema-derived field labels yet
(planned as a follow-up). Wrapped in markers so a provider's text is read as
retrieved data, never as instructions, matching moderation's convention.
"""

from __future__ import annotations

from dss.core.provider_discovery.models import DiscoveredAnswer

_BEGIN_MARKER = "<BEGIN RETRIEVED DATA>"
_END_MARKER = "<END RETRIEVED DATA>"


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
    lines = [_BEGIN_MARKER]
    for key, value in answer.attributes.items():
        lines.extend(_render_field(key, value, indent=""))
    lines.append(_END_MARKER)
    return "\n".join(lines)
