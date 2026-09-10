"""Renders an ask's candidates and their filterable fields as markdown.

The model must see this before calling select: the tool's argument type is
a bare ``dict``, so nothing in the tool's own schema tells the model which
fields are valid for a given capability, nor which values a provider serves.

Field names are DSS-controlled (our own discovery config, schema pack field
names). The advertised values are not: they are a provider's own catalog text,
off the wire. They are rendered unwrapped anyway, unlike
``render_answer_as_markdown``'s marker-wrapped answers, because this is a tool
result rather than prompt content and the model must read it as instructions
about what it may send. That is a deliberate narrowing of the "provider text
is data" rule, and it holds only while what is rendered stays a short
vocabulary — see the note on ``_render_advertised``.
"""

from __future__ import annotations

from collections.abc import Mapping

from dss.core.planner.validation import DomainSchema
from dss.core.provider_discovery.models import ProviderCapability


def _render_item(item: object) -> str:
    """One advertised entry. A code/name pair renders as ``78=Tomato``.

    The model needs both halves: the name to match the farmer's word, the code
    to send. Either alone is unusable.
    """

    if isinstance(item, dict) and "code" in item:
        name = item.get("name")
        return f"{item['code']}={name}" if name else str(item["code"])
    return str(item)


def _render_advertised(advertised: Mapping[str, object]) -> list[str]:
    """The provider's own vocabularies, one line per advertised list.

    Only lists. A resource advertises two kinds of thing side by side: a set
    of values the model may choose from (``supportedCommodities``), and a fact
    about the provider (``historyPeriod: P1Y``, ``historicalDataAvailable:
    true``). Only the first can appear in a ``select`` call, and the real
    MandiPrice catalog carries three of the second to two of the first — so
    rendering both put mostly unusable text under a heading promising values
    the provider serves.

    A list is the signal rather than a ``supported*`` prefix: that prefix is
    MandiPrice's own naming and binds no other pack.

    ``str()`` on an unrecognised item rather than raising. This is network
    data of unknown shape — a pack may advertise something never seen here —
    and, like the missing-schema skip below, a formatting problem must not be
    able to end a farmer's turn.
    """

    vocabularies = {
        field: value
        for field, value in advertised.items()
        if isinstance(value, list) and value
    }
    if not vocabularies:
        return []
    return [
        "  this provider serves only these values:",
        *(
            f"    {field}: {', '.join(_render_item(item) for item in value)}"
            for field, value in vocabularies.items()
        ),
    ]


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
        lines.extend(_render_advertised(candidate.advertised))

    if not lines:
        return "No candidates found for this ask."
    return "\n".join(lines)
