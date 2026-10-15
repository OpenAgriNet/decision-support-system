"""Behaviour for provider_preferences. Plain Python in, plain Python out.

One job: take what discovery found and leave behind only the providers this
client ranked, in the order they ranked them. Everything downstream — the
planner's candidate list, `describe_capability`, the Direct answers the
catalog already held — reads the result and needs to know nothing about
clients or preferences.

**A ranking is exclusive.** A provider discovery found that the ranking does
not name is dropped, not appended. The alternative — rank the named ones and
keep the rest underneath — would mean a client's list only ever changed
*which* provider answered first, never *whether* an unvetted one could
answer at all, and vetting is the point (ADR-0019).

**No ranking leaves the ask alone.** A topic nobody configured keeps every
candidate discovery found, in discovery's own order, which is the behaviour
that existed before any preference did.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Protocol

from dss.core.intent.models import Intent
from dss.core.provider_discovery.models import DiscoveryResult
from dss.core.provider_preferences.models import (
    AskPriority,
    ClientPreference,
    ProviderId,
)


class _HasProvider(Protocol):
    """What ordering needs off a candidate: its provider id.

    `ProviderCapability` and `DiscoveredAnswer` share nothing but this, and
    both have to be ordered the same way — a client that ranked a provider
    out of its weather list must not then be served that provider's Direct
    answer, which no `select` call would have been needed for.
    """

    provider_id: str


def prioritise(
    discovery: DiscoveryResult,
    *,
    intent: Intent,
    preference: ClientPreference,
) -> DiscoveryResult:
    """Reorder and filter discovery's findings to this client's ranking.

    Both halves of a finding are governed: `capabilities`, which the planner
    calls, and `answers`, which the catalog already holds. An ask whose topic
    carries no ranking passes through untouched.

    The record of what happened rides back on `DiscoveryResult.priorities` —
    one entry per ask, whether or not a ranking applied. It is the only thing
    downstream that can tell "this provider was never asked" from "this
    provider had nothing".
    """

    answers = dict(discovery.answers)
    capabilities = dict(discovery.capabilities)
    priorities: dict[int, AskPriority] = {}

    for index, ask in enumerate(intent.asks):
        topic = ask.subject_categories.value
        ranking = preference.ranking_for(topic)
        found = (
            *discovery.capabilities.get(index, ()),
            *discovery.answers.get(index, ()),
        )
        priorities[index] = AskPriority(
            ask_index=index,
            topic=topic,
            client_id=preference.client_id,
            source=preference.source_for(topic) if ranking else None,
            ranking=ranking,
            kept=_kept(found, ranking) if ranking else (),
            dropped=_dropped(found, ranking) if ranking else (),
        )
        if not ranking:
            continue
        # Only where discovery left something to filter. Writing a key for an
        # ask it never found anyone for would turn "this ask is absent from
        # the result" into "this ask found nobody" — the same thing to the
        # farmer, a different line in `discovery_text` and one more key for
        # every later reader to account for.
        if index in discovery.capabilities:
            capabilities[index] = _ordered(discovery.capabilities[index], ranking)
        if index in discovery.answers:
            answers[index] = _ordered(discovery.answers[index], ranking)

    return replace(
        discovery, answers=answers, capabilities=capabilities, priorities=priorities
    )


def _ordered[T: _HasProvider](
    items: Sequence[T], ranking: tuple[ProviderId, ...]
) -> tuple[T, ...]:
    """The ranked items, best provider first; the rest dropped.

    Stable within one provider: a provider offering three resources for the
    same ask keeps them in the order discovery returned them, because the
    ranking says nothing about which of a provider's own resources to prefer.
    """

    rank = {provider: position for position, provider in enumerate(ranking)}
    kept = [item for item in items if item.provider_id in rank]
    return tuple(sorted(kept, key=lambda item: rank[item.provider_id]))


def _kept(
    found: Sequence[_HasProvider], ranking: tuple[ProviderId, ...]
) -> tuple[ProviderId, ...]:
    """Ranked providers discovery actually found, in ranking order.

    Deduplicated: one provider can offer several resources for one ask, and
    this counts providers, not resources.
    """

    present = {item.provider_id for item in found}
    return tuple(provider for provider in ranking if provider in present)


def _dropped(
    found: Sequence[_HasProvider], ranking: tuple[ProviderId, ...]
) -> tuple[ProviderId, ...]:
    """Providers discovery found that the ranking excluded, first seen first.

    `dict.fromkeys` rather than a set: the order is what an operator reads,
    and a set's would change between runs.
    """

    return tuple(
        dict.fromkeys(
            item.provider_id for item in found if item.provider_id not in ranking
        )
    )


def ranking_for_ask(
    discovery: DiscoveryResult, *, ask_index: int
) -> AskPriority | None:
    """The ranking that governed this ask, or ``None`` if none did.

    ``None`` covers three states that have to behave identically: no
    preferences are mounted, the ask's topic carries no ranking, and the
    result predates preferences entirely (an unwired deployment, a test
    building a `DiscoveryResult` by hand). Each one means "do what the system
    did before a ranking existed", and a caller that branched on `priorities`
    directly would have to spell all three out.
    """

    priority = discovery.priorities.get(ask_index)
    if priority is None or not priority.applied:
        return None
    return priority
