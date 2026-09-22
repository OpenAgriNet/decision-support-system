"""The orchestrator — it holds the turn and calls each component in order
(design doc §4). Components never call each other; every one takes plain
objects and returns plain objects.

Order is code, not config. What this function does *is* the sequence.

Four components so far: intent, scheme enrichment, moderation, provider
discovery. Enrichment sits between intent and discovery — it needs the
classified asks, and discovery routes on what it leaves behind. Intent and
moderation are independent — moderation judges harm on the raw query, intent
classifies capability need, and neither consumes the other's output — so they
run concurrently (ADR-0003) and the turn's latency is the slower of the two
rather than their sum. Discovery needs ``Intent.asks``, so it chains off
intent.

Still to come, in order: the planner agent (returns ``Evidence``), then the
composer (turns ``Evidence`` into text). So ``TurnResult`` is a staging shape
— the design's orchestrator hands a composed response back to the API, not a
bag of intermediate findings. It grows a field per component until the
composer lands.

Each component is bound to its own model via the ``LLMProvider`` it is handed
(see ``config/settings.py``), so a deployment can point intent and moderation at
different models.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import anyio
from pydantic import BaseModel, ConfigDict

from dss.core.enrichment.service import resolve_scheme_subjects
from dss.core.intent.models import Intent
from dss.core.intent.service import classify_intent
from dss.core.moderation.models import (
    ModerationContext,
    ModerationDecision,
    Outcome,
)
from dss.core.moderation.service import moderate
from dss.core.policy.models import Policy
from dss.core.provider_discovery.models import DiscoveryResult
from dss.core.shared.models import UserTurn
from dss.observability.stages import Stage
from dss.observability.trace_log import log_event, trace_component
from dss.orchestration.discovery import DiscoverProviders
from dss.ports.llm import LLMProvider
from dss.ports.scheme_catalog import SchemeCatalog


def _nothing_discovered() -> DiscoveryResult:
    """An empty result, fresh each time.

    A factory rather than a module constant: ``DiscoveryResult`` is frozen,
    but its ``answers``/``capabilities``/``failures`` are plain dicts, and
    frozen stops you reassigning a field, not mutating the dict inside it.
    ``_apply_expiry_filter`` writes ``answers[ask_index]`` in place on
    whatever result it is handed. Nothing routes this one there today, but a
    single shared object means one such write would corrupt every subsequent
    rejected turn in the process.
    """

    return DiscoveryResult(answers={}, capabilities={}, failures={}, events=())


def _enrich(
    intent: Intent,
    turn: UserTurn,
    catalog: SchemeCatalog | None,
    fuzzy_threshold: float | None,
) -> Intent:
    """Resolve any scheme the turn names to its official name.

    Every resolution is logged because the rewrite is otherwise invisible: the
    ask reaching discovery no longer holds the farmer's words. Only
    catalog-authored text is logged, so nothing here is PII. ``None`` is a
    real deployment state — no catalog mounted, farmer's words kept.
    """

    if catalog is None:
        return intent

    resolution = resolve_scheme_subjects(
        intent,
        turn.original_query,
        catalog.aliases(),
        fuzzy_threshold=fuzzy_threshold,
    )
    for match in resolution.matches:
        log_event(
            "enrichment",
            turn.transaction_id,
            event="scheme_resolved",
            alias=match.matched_alias,
            scheme_code=match.scheme.code,
            # A wrong fuzzy hit is the failure this can produce.
            fuzzy=match.fuzzy,
        )
    return resolution.intent


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
    discovery: DiscoveryResult


async def run_turn(
    turn: UserTurn,
    *,
    intent_llm: LLMProvider,
    moderation_llm: LLMProvider,
    policies: Sequence[Policy],
    discover_providers: DiscoverProviders,
    scheme_catalog: SchemeCatalog | None = None,
    scheme_fuzzy_threshold: float | None = None,
    now: datetime | None = None,
) -> TurnResult:
    """Classify intent, moderate the turn, and find who can answer it.

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
    """

    intent = Intent()
    discovery = _nothing_discovered()
    decision: ModerationDecision | None = None

    async def classify_then_discover() -> None:
        nonlocal intent, discovery
        with trace_component(Stage.INTENT, turn.transaction_id):
            intent = await classify_intent(turn, intent_llm)
        with trace_component(Stage.ENRICHMENT, turn.transaction_id):
            intent = _enrich(intent, turn, scheme_catalog, scheme_fuzzy_threshold)
        with trace_component(Stage.DISCOVERY, turn.transaction_id):
            discovery = await discover_providers(
                intent, turn, now=now or datetime.now(UTC)
            )

    async def run_moderation() -> None:
        nonlocal decision
        with trace_component(Stage.MODERATION, turn.transaction_id):
            decision = await moderate(
                ModerationContext(turn=turn), policies, moderation_llm
            )

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(run_moderation)
        task_group.start_soon(classify_then_discover)

    # Type narrowing only, safe to strip under `python -O`: the task group
    # either ran both branches to completion or raised out of the `async
    # with`, so reaching here means moderation set this.
    assert decision is not None
    if decision.outcome is not Outcome.PROCEED:
        return TurnResult(
            intent=Intent(), decision=decision, discovery=_nothing_discovered()
        )
    return TurnResult(intent=intent, decision=decision, discovery=discovery)
