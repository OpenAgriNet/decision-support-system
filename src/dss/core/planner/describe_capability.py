"""Renders an ask's candidates and their filterable fields as markdown.

The model must see this before calling select: the tool's argument type is
a bare ``dict``, so nothing in the tool's own schema tells the model which
fields are valid for a given capability. This is DSS-controlled data (our
own discovery config, schema pack field names) — not farmer- or
provider-supplied text — so it carries none of ``render_answer_as_markdown``'s
marker-wrapping; that convention is for untrusted third-party content only.
"""

from __future__ import annotations

from dss.core.planner.validation import DomainSchema
from dss.core.provider_discovery.models import ProviderCapability


def render_candidates_as_markdown(
    candidates: tuple[ProviderCapability, ...],
    *,
    schemas: dict[str, DomainSchema],
) -> str:
    if not candidates:
        return "No candidates found for this ask."

    lines = []
    for candidate in candidates:
        schema = schemas[candidate.capability]
        lines.append(f"- {candidate.provider_name}")
        lines.append(f"  resource_id: {candidate.resource_id}")
        lines.append(f"  fields you may set: {', '.join(schema.filterable)}")
    return "\n".join(lines)
