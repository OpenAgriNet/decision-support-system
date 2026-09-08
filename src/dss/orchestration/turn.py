"""The orchestrator — it holds the turn and calls each component in order
(design doc §4). Components never call each other; every one takes plain
objects and returns plain objects.

Order is code, not config. What this function does *is* the sequence.

Three components so far: intent, moderation, provider discovery. Intent and
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
from dss.orchestration.discovery import DiscoverProviders
from dss.ports.llm import LLMProvider

_NOTHING_DISCOVERED = DiscoveryResult(
    answers={}, capabilities={}, failures={}, events=()
)


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
    discovery = _NOTHING_DISCOVERED
    decision: ModerationDecision | None = None

    async def classify_then_discover() -> None:
        nonlocal intent, discovery
        intent = await classify_intent(turn, intent_llm)
        discovery = await discover_providers(intent, turn, now=now or datetime.now(UTC))

    async def run_moderation() -> None:
        nonlocal decision
        decision = await moderate(
            ModerationContext(turn=turn), policies, moderation_llm
        )

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(run_moderation)
        task_group.start_soon(classify_then_discover)

    assert decision is not None  # the task group joined, so both branches ran
    if decision.outcome is not Outcome.PROCEED:
        return TurnResult(
            intent=Intent(), decision=decision, discovery=_NOTHING_DISCOVERED
        )
    return TurnResult(intent=intent, decision=decision, discovery=discovery)
