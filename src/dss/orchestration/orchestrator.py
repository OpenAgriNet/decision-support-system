"""The orchestrator — one function that runs a turn (design v2 §4).

The orchestrator holds the turn and calls each component in order. Components never
call each other: every one takes plain objects and returns plain objects, and this
module is the only place that knows the sequence. Order is code, not config.

    intent ∥ moderation      run together — both need only the turn (ADR-0003)
          │
          ▼
      (gate on the verdict — the barrier)
          │  proceed
          ▼
      provider discovery      read-only; only runs once moderation has cleared
          │
          ▼
      planner agent           builds *and runs* the plan (owns execution)
          │
          ▼
       TurnResult

Nothing side-effecting runs until moderation has cleared: the planner — which is
also what touches the outside world — is reached only on ``PROCEED``, and the three
non-answer outcomes return before it (design v2 §3).

Downstream response composition and channel shaping are separate components, not
yet built; this orchestrator stops at the planner's result and does not stub them.

Composition, not config, decides which components run: the callables are wired once
in :func:`build_components` (or a test). This module never imports the orchestration
framework — it composes plain async callables behind ``ports``. Concurrency is
``anyio`` (ADR-0005), consistent with the rest of ``core``/``adapters``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import partial

import anyio
from pydantic import BaseModel, ConfigDict

from dss.core.intent.models import Intent
from dss.core.intent.service import classify_intent
from dss.core.moderation.models import ModerationContext, ModerationDecision, Outcome
from dss.core.moderation.service import moderate as moderate_service
from dss.core.planning.models import Plan
from dss.core.planning.service import plan_turn
from dss.core.policy.models import Policy
from dss.core.provider_discovery.models import DiscoveryResult
from dss.core.shared.models import UserTurn
from dss.orchestration.discovery import DiscoverProviders
from dss.ports.llm import LLMProvider


@dataclass(frozen=True)
class OrchestratorComponents:
    """The components a turn runs through, each already bound to its own model,
    ports and config. Swapping an implementation is a change here, not in
    ``run_turn``."""

    classify: Callable[[UserTurn], Awaitable[Intent]]
    moderate: Callable[[UserTurn], Awaitable[ModerationDecision]]
    discover_providers: Callable[
        [Intent, UserTurn, datetime], Awaitable[DiscoveryResult]
    ]
    plan: Callable[
        [UserTurn, Intent, DiscoveryResult, ModerationDecision], Awaitable[Plan]
    ]


class TurnResult(BaseModel):
    """The turn's finding. ``plan`` is ``None`` when the turn did not proceed. On a
    non-``PROCEED`` outcome ``intent`` is blanked (ADR-0003): a refused turn surfaces
    no intent read off the text it refused."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: Outcome
    decision: ModerationDecision
    intent: Intent
    plan: Plan | None = None


def outcome_for(decision: ModerationDecision, plan: Plan) -> Outcome:
    """Which of the four ways out the turn took. Only the orchestrator can decide
    this — it is the only thing that sees both the verdict and the plan. Moderation's
    outcome wins; on ``PROCEED`` the plan's steps/missing pair decides (design v2
    §6.6 table)."""

    if decision.outcome is not Outcome.PROCEED:
        return decision.outcome
    if plan.steps:
        return Outcome.PROCEED  # run it (missing inputs, if any, asked alongside)
    if plan.missing:
        return Outcome.CLARIFY  # pure clarification
    return Outcome.NO_MATCH  # nothing to do


async def run_turn(
    turn: UserTurn,
    components: OrchestratorComponents,
    *,
    now: datetime,
) -> TurnResult:
    """Run one turn: classify and moderate concurrently, gate on the verdict, then
    discover and plan. ``now`` is passed in rather than read from the clock so a turn
    is reproducible (the repo threads time explicitly)."""

    # Intent and moderation need only the turn, so they run together (ADR-0003).
    box: dict[str, Intent | ModerationDecision] = {}

    async def _classify() -> None:
        box["intent"] = await components.classify(turn)

    async def _moderate() -> None:
        box["decision"] = await components.moderate(turn)

    async with anyio.create_task_group() as tg:
        tg.start_soon(_classify)
        tg.start_soon(_moderate)

    intent: Intent = box["intent"]  # type: ignore[assignment]
    decision: ModerationDecision = box["decision"]  # type: ignore[assignment]

    # The barrier. On any non-PROCEED outcome nothing further runs — no discovery,
    # no planner, so no outside call — and the classified intent is discarded.
    if decision.outcome is not Outcome.PROCEED:
        return TurnResult(
            outcome=decision.outcome, decision=decision, intent=Intent(), plan=None
        )

    # Past the barrier. Discovery is read-only; the planner builds and runs the plan.
    discovered = await components.discover_providers(intent, turn, now)
    plan = await components.plan(turn, intent, discovered, decision)

    return TurnResult(
        outcome=outcome_for(decision, plan),
        decision=decision,
        intent=intent,
        plan=plan,
    )


def build_components(
    *,
    intent_llm: LLMProvider,
    moderation_llm: LLMProvider,
    policies: Sequence[Policy],
    discover_providers: DiscoverProviders,
) -> OrchestratorComponents:
    """Wire the real components — intent, moderation, provider discovery — and the
    planner into one bundle, each bound to its model, ports and config. The
    entrypoint calls this once at startup; ``run_turn`` reuses it for every turn."""

    async def moderate(turn: UserTurn) -> ModerationDecision:
        return await moderate_service(
            ModerationContext(turn=turn), policies, moderation_llm
        )

    return OrchestratorComponents(
        classify=partial(classify_intent, llm=intent_llm),
        moderate=moderate,
        discover_providers=discover_providers,
        plan=plan_turn,
    )
