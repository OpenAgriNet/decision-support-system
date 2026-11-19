"""The understand phase of a turn: intent, the place lookup, settlement and
discovery, next to moderation.

Order is code, not config. What this function does is the sequence.

Intent and moderation run at the same time (ADR-0003), so the turn waits only
for the slower one. The rest follows intent. The place lookup fills each ask's
place. Settlement decides which asks have what they need. Discovery searches
only those. The planner and composer come after, in the orchestrator.

Each component gets its own ``LLMProvider`` (see ``config/settings.py``), so
intent and moderation can use different models.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from functools import cached_property
from typing import assert_never

import anyio
from pydantic import BaseModel, ConfigDict

from dss.adapters.observability.tracing import (
    set_current_span_attributes,
    set_current_span_content,
)
from dss.core.intent.models import (
    AmbiguousPlace,
    Ask,
    Intent,
    IntentClassification,
    ResolvedPlace,
    UnresolvedPlace,
)
from dss.core.intent.service import classify_intent
from dss.core.location.service import DEFAULT_NEAREST_MAX_KM, resolve_places
from dss.core.moderation.models import (
    ModerationContext,
    ModerationDecision,
    Outcome,
)
from dss.core.moderation.service import moderate
from dss.core.policy.models import Policy
from dss.core.provider_discovery.models import DiscoveryResult
from dss.core.settlement.models import (
    AskSettlement,
    CannotServe,
    NeedsClarification,
    Settled,
    Settlement,
    Slot,
    Unresolved,
    Unservable,
)
from dss.core.shared.models import TextQuery, UserTurn
from dss.observability.stages import Stage
from dss.observability.trace_log import trace_component
from dss.orchestration.discovery import DiscoverProviders
from dss.orchestration.settlement import SettleAsks
from dss.orchestration.stage_content import (
    asks_text,
    asks_without_place,
    discovery_text,
)
from dss.ports.area_lookup import AreaLookup
from dss.ports.llm import LLMProvider
from dss.ports.prompts import PromptProvider
from dss.ports.scheme_catalog import SchemeCatalog


def _nothing_discovered() -> DiscoveryResult:
    return DiscoveryResult(asks=(), events=())


def place_attributes(
    classification: IntentClassification, intent: Intent
) -> dict[str, str | int]:
    """What happened to each ask's place, for the `location` span.

    Counts and names only. Never coordinates. Langfuse shows the last two
    keys in its Input and Output boxes.
    """

    resolved = ambiguous = unresolved = none = candidates = 0
    names: list[str] = []
    outcomes: list[str] = []
    for ask in intent.asks:
        place = ask.place
        if isinstance(place, ResolvedPlace):
            resolved += 1
            # A device point can have no name.
            if place.name:
                names.append(place.name)
            outcomes.append(f"{place.name or 'device point'}: found")
        elif isinstance(place, AmbiguousPlace):
            ambiguous += 1
            candidates += len(place.candidates)
            names.append(place.unresolved_name)
            outcomes.append(f"{place.unresolved_name}: {len(place.candidates)} matches")
        elif isinstance(place, UnresolvedPlace):
            unresolved += 1
            names.append(place.unresolved_name)
            outcomes.append(f"{place.unresolved_name}: not found")
        else:
            none += 1
            outcomes.append("no place")
    asked = [ask.place_name for ask in classification.asks if ask.place_name]
    return {
        "asks_total": len(intent.asks),
        "places_resolved": resolved,
        "places_ambiguous": ambiguous,
        "places_unresolved": unresolved,
        "places_none": none,
        "ambiguous_candidates": candidates,
        "place_names": ", ".join(names),
        "langfuse.observation.input": ", ".join(asked),
        "langfuse.observation.output": "; ".join(outcomes),
    }


def settlement_attributes(
    settlements: Sequence[AskSettlement],
) -> dict[str, str | int]:
    """Slot names and states only, never a value: a value is the farmer's own
    words, and `dss.` keys reach the shared database."""

    waits = [
        f"ask {ask_settlement.ask.id} "
        f"({ask_settlement.ask.subject_category}): "
        f"{_wait_of(ask_settlement.settlement)}"
        for ask_settlement in settlements
    ]

    def count(kind: type[Settlement]) -> int:
        return sum(
            isinstance(ask_settlement.settlement, kind)
            for ask_settlement in settlements
        )

    return {
        "dss.settlement.settled": count(Settled),
        "dss.settlement.needs_clarification": count(NeedsClarification),
        "dss.settlement.cannot_serve": count(CannotServe),
        "dss.settlement.asks": "; ".join(waits),
    }


def _wait_of(settlement: Settlement) -> str:
    match settlement:
        case Settled():
            return "settled"
        case NeedsClarification(open_slots=slots):
            return ", ".join(_slot_state(slot) for slot in slots)
        case CannotServe(slot=slot):
            return _slot_state(slot)
        case unknown:
            assert_never(unknown)


def _slot_state(slot: Slot) -> str:
    match slot.state:
        case Unresolved(reason=reason) | Unservable(reason=reason):
            return f"{slot.name} {reason.value}"
        case state:
            return f"{slot.name} {type(state).__name__.lower()}"


def moderation_attributes(decision: ModerationDecision) -> dict[str, str]:
    """Why moderation decided as it did, for the `moderation` span.

    The outcome is not its own key: the turn's `status` already says it.
    Langfuse shows the last key in its Output box.
    """

    attributes: dict[str, str] = {}
    if decision.reason_code:
        attributes["reason_code"] = decision.reason_code.value
    if decision.violated_policy_id:
        attributes["violated_policy_id"] = decision.violated_policy_id
    why = ", ".join(attributes.values())
    output = f"{decision.outcome.value}: {why}" if why else decision.outcome.value
    attributes["langfuse.observation.output"] = output
    return attributes


class TurnResult(BaseModel):
    """Every finding for a turn. Moderation is the gate: on any non-``PROCEED``
    outcome the ``intent`` is blanked to an empty ``Intent()`` — a turn that will
    not proceed surfaces no classification, so a rejected/clarify turn never leaks
    an intent read off text the assistant refused to act on.

    ``discovery`` is blanked with it, for the same reason and to stay
    consistent: candidates are derived from ``Intent.asks``, so a result
    carrying providers next to an empty intent would show an effect with no
    cause on it. Empty discovery beside a non-``PROCEED`` decision says why
    it is empty; ``decision.reason_code`` says which policy fired. Nothing is
    restated inside ``DiscoveryResult`` — discovery does not know moderation
    exists.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: Intent
    decision: ModerationDecision
    # One per ask in `intent`, same order.
    settlements: tuple[AskSettlement, ...] = ()
    # One entry per settled ask.
    discovery: DiscoveryResult

    @cached_property
    def settled_asks(self) -> tuple[Ask, ...]:
        """The asks discovery and the planner work on."""

        return _settled_asks(self.settlements)

    @property
    def is_every_ask_settled(self) -> bool:
        return all(ask_settlement.is_settled for ask_settlement in self.settlements)


def _settled_asks(settlements: Sequence[AskSettlement]) -> tuple[Ask, ...]:
    return tuple(
        ask_settlement.ask
        for ask_settlement in settlements
        if ask_settlement.is_settled
    )


async def run_turn(  # noqa: PLR0913
    turn: UserTurn[TextQuery],
    *,
    intent_llm: LLMProvider,
    moderation_llm: LLMProvider,
    prompts: PromptProvider,
    policies: Sequence[Policy],
    discover_providers: DiscoverProviders,
    settle_asks: SettleAsks,
    area_lookup: AreaLookup,
    scheme_catalog: SchemeCatalog | None = None,
    nearest_max_km: float = DEFAULT_NEAREST_MAX_KM,
    now: datetime | None = None,
) -> TurnResult:
    """Classify intent, moderate the turn, settle each ask, and find who can
    answer the settled ones.

    Intent and moderation run concurrently. Discovery chains off intent and
    starts as soon as intent lands, *without* waiting for moderation: a
    discovery query is read-only and can be thrown away, so it is allowed to
    cross the barrier. The calls it wastes on a rejected turn are cheap, and
    rejection is rare.

    What may not cross is a provider call, which cannot be taken back. That
    barrier is the planner's, enforced in its ``select`` tool; this function
    decides only what a turn that will not proceed surfaces.

    Moderation still gates the result: if the turn does not proceed, both the
    classified intent and whatever discovery found are discarded.

    An unsettled ask is never searched. It waits for the farmer's answer.
    """

    intent = Intent()
    settlements: tuple[AskSettlement, ...] = ()
    discovery = _nothing_discovered()
    decision: ModerationDecision | None = None

    async def classify_then_discover() -> None:
        nonlocal intent, settlements, discovery
        classification = await _classify(
            turn, llm=intent_llm, prompts=prompts, scheme_catalog=scheme_catalog
        )
        intent = await _resolve_places(
            classification, turn, lookup=area_lookup, nearest_max_km=nearest_max_km
        )
        settlements = _settle(intent, turn, settle_asks=settle_asks)
        discovery = await _discover(
            _settled_asks(settlements),
            turn,
            discover_providers=discover_providers,
            now=now or datetime.now(UTC),
        )

    async def run_moderation() -> None:
        nonlocal decision
        decision = await _moderate(
            turn, policies=policies, llm=moderation_llm, prompts=prompts
        )

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(run_moderation)
        task_group.start_soon(classify_then_discover)

    # Type narrowing only, safe to strip under `python -O`: the task group
    # either ran both branches to completion or raised out of the `async
    # with`, so reaching here means moderation set this.
    assert decision is not None  # noqa: S101 - type narrowing only
    if decision.outcome is not Outcome.PROCEED:
        return TurnResult(
            intent=Intent(), decision=decision, discovery=_nothing_discovered()
        )
    return TurnResult(
        intent=intent,
        decision=decision,
        settlements=settlements,
        discovery=discovery,
    )


async def _classify(
    turn: UserTurn[TextQuery],
    *,
    llm: LLMProvider,
    prompts: PromptProvider,
    scheme_catalog: SchemeCatalog | None,
) -> IntentClassification:
    with trace_component(Stage.INTENT, turn.transaction_id) as exit_fields:
        classification = await classify_intent(
            turn,
            llm,
            prompts,
            scheme_catalog.schemes() if scheme_catalog is not None else (),
        )
        exit_fields["asks"] = asks_without_place(classification.asks)
        set_current_span_content(
            input=turn.input.text, output=asks_text(classification.asks)
        )
    return classification


async def _resolve_places(
    classification: IntentClassification,
    turn: UserTurn[TextQuery],
    *,
    lookup: AreaLookup,
    nearest_max_km: float,
) -> Intent:
    with trace_component(Stage.LOCATION, turn.transaction_id):
        intent = await resolve_places(
            classification, turn, lookup=lookup, nearest_max_km=nearest_max_km
        )
        set_current_span_attributes(**place_attributes(classification, intent))
    return intent


def _settle(
    intent: Intent, turn: UserTurn[TextQuery], *, settle_asks: SettleAsks
) -> tuple[AskSettlement, ...]:
    with trace_component(Stage.SETTLEMENT, turn.transaction_id):
        settlements = settle_asks(intent.asks)
        set_current_span_attributes(**settlement_attributes(settlements))
    return settlements


async def _discover(
    settled_asks: Sequence[Ask],
    turn: UserTurn[TextQuery],
    *,
    discover_providers: DiscoverProviders,
    now: datetime,
) -> DiscoveryResult:
    """Nothing settled means nothing to search."""

    if not settled_asks:
        return _nothing_discovered()
    with trace_component(Stage.DISCOVERY, turn.transaction_id):
        discovery = await discover_providers(settled_asks, turn, now=now)
        set_current_span_content(
            input=asks_text(settled_asks), output=discovery_text(discovery)
        )
    return discovery


async def _moderate(
    turn: UserTurn[TextQuery],
    *,
    policies: Sequence[Policy],
    llm: LLMProvider,
    prompts: PromptProvider,
) -> ModerationDecision:
    with trace_component(Stage.MODERATION, turn.transaction_id):
        decision = await moderate(ModerationContext(turn=turn), policies, llm, prompts)
        set_current_span_attributes(**moderation_attributes(decision))
        set_current_span_content(input=turn.input.text)
    return decision
