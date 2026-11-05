"""Which asks may go on without a place.

An ``Ask`` whose ``place`` is ``None`` says nothing about whether one was
needed: "how do I grow potatoes" and "will it rain" both arrive that way. The
schema pack settles it (its place-bound set, the same fact as
``DomainSchema.needs_place``), and a pack is only
known once discovery has named the options for an ask — so this is read
here, over discovery, not at intent time.
"""

from __future__ import annotations

from collections.abc import Collection

from dss.core.intent.models import Intent
from dss.core.provider_discovery.models import DiscoveryResult


def place_optional_asks(
    intent: Intent,
    discovery: DiscoveryResult,
    place_bound_types: Collection[str],
    *,
    awaiting: Collection[int] = frozenset(),
) -> frozenset[int]:
    """The indexes of the asks that name no place and are not failed for it:
    some option discovered for them comes from a pack that needs none, or no
    option was found at all. The second is not a place failure — nobody
    serves the ask, and a place would not change that — so it ends as
    unserved rather than as a question the farmer's reply cannot finish.
    An ask in ``awaiting`` needs none either: its form carries what its call
    needs.

    The pack's own fact decides, so a type no pack holds a place path for
    needs none — including a desk the planner has no schema for."""

    return frozenset(
        index
        for index, ask in enumerate(intent.asks)
        if ask.place is None and _needs_no_place(index, discovery, place_bound_types)
    ) | frozenset(awaiting)


def _needs_no_place(
    index: int, discovery: DiscoveryResult, place_bound_types: Collection[str]
) -> bool:
    options = (
        *discovery.answers.get(index, ()),
        *discovery.capabilities.get(index, ()),
    )
    return not options or any(
        option.capability not in place_bound_types for option in options
    )
