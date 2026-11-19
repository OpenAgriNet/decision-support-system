"""Hands settlement the facts it needs about each ask: what the schema packs
say of a place, and which forms the ask's category has. Settlement itself
reads no pack and no registry."""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Sequence
from functools import partial

from dss.core.forms.models import FormContext
from dss.core.intent.models import Ask, SubjectCategory
from dss.core.provider_discovery.models import CapabilityIndex, NetworkSchemaType
from dss.core.provider_discovery.service import (
    CapabilityIndexSource,
    capability_types_for,
)
from dss.core.schema_packs.service import is_place_needed
from dss.core.settlement import service as settlement
from dss.core.settlement.models import AskRequirements, AskSettlement
from dss.core.settlement.requirements import build_ask_requirements

# One per ask, in ask order.
type SettleAsks = Callable[[Sequence[Ask]], tuple[AskSettlement, ...]]


# A category's forms.
type FormContextLookup = Callable[[SubjectCategory], Iterable[FormContext]]


def build_settle_asks(
    index: CapabilityIndexSource, contexts_for: FormContextLookup
) -> SettleAsks:
    """Reads the index on every turn. It can refresh while the app runs."""

    def settle(asks: Sequence[Ask]) -> tuple[AskSettlement, ...]:
        requirements_for = partial(
            _read_ask_requirements,
            index=index.current(),
            place_bound_types=index.place_bound_types(),
            contexts_for=contexts_for,
        )
        return settlement.settle_asks(asks, requirements_for=requirements_for)

    return settle


def _read_ask_requirements(
    ask: Ask,
    *,
    index: CapabilityIndex,
    place_bound_types: Collection[NetworkSchemaType],
    contexts_for: FormContextLookup,
) -> AskRequirements:
    pack_types = capability_types_for(ask, index)
    return build_ask_requirements(
        ask,
        needs_place=is_place_needed(pack_types, place_bound_types),
        forms=contexts_for(ask.subject_categories),
    )
