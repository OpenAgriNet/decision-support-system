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
is data" rule, and it holds only because what reaches here is a short
vocabulary of governed values — never the provider's descriptive prose. The
test that keeps it that way is in ``_describes_own_content``.
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


def _describes_own_content(field: str, filterable: tuple[str, ...]) -> bool:
    """Whether an advertised field is the provider describing what it holds,
    rather than a vocabulary the model must choose from.

    The name is the signal: a vocabulary is named apart from the filter it
    governs (``supportedCommodities`` for ``commodity.code``), so advertising
    under the very path you would filter on means publishing content, not
    enumerating choices. ``topics`` is the case that bit.

    Shape is not the signal — ``supportedParameters: ["Rainfall"]`` is a real
    vocabulary of bare strings. Exact match, not prefix: it cannot separate
    ``agricultureSubjects`` (a vocabulary under a compound filterable path)
    from a content field under one, and only the exact case is seen on the
    wire. See ADR-0007.
    """

    return field in filterable


def _render_advertised(
    advertised: Mapping[str, object], filterable: tuple[str, ...]
) -> list[str]:
    """The provider's own vocabularies, one line per advertised list.

    Only lists. A resource advertises a set of values the model may choose
    from (``supportedCommodities``) alongside facts about itself
    (``historyPeriod: P1Y``, ``historicalDataAvailable: true``). Only the
    first can appear in a ``select`` call, and the real MandiPrice catalog
    carries three of the second to two of the first — so rendering both put
    mostly unusable text under a heading promising values the provider serves.

    A list is the signal rather than a ``supported*`` prefix: that prefix is
    MandiPrice's own naming and binds no other pack. It only separates a
    vocabulary from a scalar fact, though — separating it from the provider's
    own content is ``_describes_own_content``'s job.

    ``str()`` on an unrecognised item rather than raising. This is network
    data of unknown shape — a pack may advertise something never seen here —
    and, like the missing-schema skip below, a formatting problem must not be
    able to end a farmer's turn.
    """

    vocabularies = {
        field: value
        for field, value in advertised.items()
        if isinstance(value, list)
        and value
        and not _describes_own_content(field, filterable)
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
        lines.extend(_render_advertised(candidate.advertised, schema.filterable))

    if not lines:
        return "No candidates found for this ask."
    return "\n".join(lines)
