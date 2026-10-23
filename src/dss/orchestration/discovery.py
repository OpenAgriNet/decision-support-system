"""Composition root for the discovery client adapter."""

from __future__ import annotations

from datetime import datetime
from functools import partial
from typing import Protocol

import httpx

from dss.adapters.discovery.client import HttpCapabilityDiscovery, SchemaContextSource
from dss.adapters.observability.tracing import set_current_span_attributes
from dss.core.intent.models import Intent
from dss.core.provider_discovery.models import DiscoveryResult
from dss.core.provider_discovery.service import (
    CapabilityIndexSource,
    discover_providers,
)
from dss.core.provider_preferences.service import prioritise
from dss.core.shared.models import UserTurn
from dss.observability.trace_log import log_event
from dss.ports.discovery import CapabilityDiscovery
from dss.ports.provider_preferences import ProviderPreferences


def build_capability_discovery(
    client: httpx.AsyncClient,
    base_url: str,
    schema_pack_cache: SchemaContextSource,
) -> HttpCapabilityDiscovery:
    return HttpCapabilityDiscovery(
        client=client,
        base_url=base_url,
        schema_pack_cache=schema_pack_cache,
    )


class DiscoverProviders(Protocol):
    async def __call__(
        self, intent: Intent, turn: UserTurn, *, now: datetime
    ) -> DiscoveryResult: ...


def build_discover_providers(
    discovery: CapabilityDiscovery,
    schema_pack_cache: CapabilityIndexSource,
    radius_m: int,
    preferences: ProviderPreferences,
) -> DiscoverProviders:
    """Bakes in the built adapter, cache, configured radius, and the sender
    ranking source, so callers only ever supply what changes per turn: intent,
    turn, and now.

    Discovery only reads ``ask.place``; ``core.location.resolve_places`` fills
    it before discovery runs.

    **The sender's ranking is applied here**, on the way out, rather than at
    each place that consumes a candidate (ADR-0019). The turn already carries
    the sender id and the intent already carries each ask's capability, so
    the first point where both are in hand — and filtering once at the source
    means every later reader (the no-match check, the planner's candidate
    list, ``describe_capability``, the Direct answers the catalog held) sees
    the ranked set without knowing a sender expressed a preference at all.

    The fan-out reports two counts, onto the span already open around it —
    `trace_component("discovery")` in `turn.py` brackets exactly this call, so
    a span of its own would start and end with that one. `core/` may not open
    a span itself (ADR-0012), which is why the counts are read out here.

    That stage span stays green even when a provider fails. An ask nobody could
    serve is a normal outcome, not a broken stage: the other asks still answer
    and the turn still succeeds. The failed call's own `dss.discover` span is
    the red one. Counts rather than a status, because span status has no value
    between OK and ERROR and "one of three failed" needs one — and a count can
    be filtered and graphed, which a colour cannot.
    """

    bound = partial(
        discover_providers,
        discovery=discovery,
        schema_pack_cache=schema_pack_cache,
        radius_m=radius_m,
    )

    async def discover(
        intent: Intent, turn: UserTurn, *, now: datetime
    ) -> DiscoveryResult:
        result = await bound(intent, turn, now=now)
        preference = await preferences.for_sender(
            turn.sender_id, transaction_id=turn.transaction_id
        )
        result = prioritise(result, intent=intent, preference=preference)
        _report(result, turn=turn)
        # `asks_total`, not "asks queried": the core service seeds `failures`
        # with every unresolved ask before any query goes out, so an ask whose
        # capability never resolved is a key here too. Calling it "queried"
        # would flatter an `asks_failed / asks_total` panel by the number of
        # asks nobody could even look for.
        set_current_span_attributes(
            asks_total=len(result.failures),
            asks_failed=sum(1 for failures in result.failures.values() if failures),
            sender_id=turn.sender_id or "",
            asks_ranked=sum(1 for p in result.priorities.values() if p.applied),
            providers_dropped=sum(len(p.dropped) for p in result.priorities.values()),
        )
        return result

    return discover


def _report(result: DiscoveryResult, *, turn: UserTurn) -> None:
    """Say what the ranking did to each ask, on the turn's own key.

    One line per ask that a ranking governed. Ids and counts only — a
    provider id and a subject category are configuration, never the farmer's
    words (CONVENTIONS.md), so this is safe at INFO where a response body
    would not be.

    `dropped` is the line's reason for existing: a provider discovery found
    and the ranking excluded is invisible everywhere else, and it is the
    first thing to look at when a sender's answers come back thinner than
    another sender's for the same question.
    """

    for priority in result.priorities.values():
        if not priority.applied:
            continue
        log_event(
            "provider_preferences",
            turn.transaction_id,
            event="ranking_applied",
            ask_index=priority.ask_index,
            capability=priority.capability,
            sender_id=priority.sender_id or "-",
            source=priority.source,
            ranked=_ids(priority.ranking),
            found=_ids(priority.kept),
            dropped=_ids(priority.dropped),
        )


def _ids(providers: tuple[str, ...]) -> str:
    """Provider ids as one greppable field. `-` rather than an empty value, so
    a reader can tell the field was logged and was empty."""

    return ",".join(providers) or "-"
