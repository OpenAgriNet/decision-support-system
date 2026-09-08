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
    lines = []
    for candidate in candidates:
        # Skipped rather than raised. Discovery reports what the network
        # offers; the index is built from the packs on disk, and the two can
        # disagree — a skipped pack leaves the index without a @type the
        # network still advertises. Raising here lost the ask's *other*
        # candidates, which were usable, and this tool touches nothing
        # external so it must not be able to end a turn. The skipped pack is
        # already reported at refresh time as SchemaPackSkipped.
        schema = schemas.get(candidate.capability)
        if schema is None:
            continue
        lines.append(f"- {candidate.provider_name}")
        lines.append(f"  resource_id: {candidate.resource_id}")
        lines.append(f"  fields you may set: {', '.join(schema.filterable)}")

    if not lines:
        return "No candidates found for this ask."
    return "\n".join(lines)
