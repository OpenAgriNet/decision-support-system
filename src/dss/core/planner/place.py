"""Which asks may go on without a place.

An ``Ask`` whose ``place`` is ``None`` says nothing about whether one was
needed: "how do I grow potatoes" and "will it rain" both arrive that way. The
schema pack settles it (``DomainSchema.needs_place``), and a pack is only
known once discovery has named the options for an ask — so this is read
here, over discovery, not at intent time.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping

from dss.core.intent.models import Intent
from dss.core.planner.validation import DomainSchema
from dss.core.provider_discovery.models import DiscoveryResult


def place_optional_asks(
    intent: Intent,
    discovery: DiscoveryResult,
    schemas: Mapping[str, DomainSchema],
    *,
    awaiting: Collection[int] = frozenset(),
) -> frozenset[int]:
    """The indexes of the asks that name no place and are not failed for it:
    some option discovered for them comes from a pack that needs none, or no
    option was found at all. The second is not a place failure — nobody
    serves the ask, and a place would not change that — so it ends as
    unserved rather than as a question the farmer's reply cannot finish.
    An ask in ``awaiting`` needs none either: its form carries what its call
    needs."""

    optional: set[int] = set()
    for index, ask in enumerate(intent.asks):
        if ask.place is not None:
            continue
        options = (
            *discovery.answers.get(index, ()),
            *discovery.capabilities.get(index, ()),
        )
        if not options or any(
            _needs_no_place(option.capability, schemas) for option in options
        ):
            optional.add(index)
    return frozenset(optional) | frozenset(awaiting)


def _needs_no_place(schema_type: str, schemas: Mapping[str, DomainSchema]) -> bool:
    """A pack the planner has no schema for cannot be called anyway; saying
    it needs a place keeps the question the farmer saw before this rule."""

    schema = schemas.get(schema_type)
    return schema is not None and not schema.needs_place
