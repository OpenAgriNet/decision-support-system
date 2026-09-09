"""The orchestrator — the live turn runner (design doc §4).

It holds the turn and calls each real component in order, then streams the
result to the transport. Components never call each other; every one takes
plain objects and returns plain objects, and this module is the only place
that knows the sequence. Order is code, not config.

    intent ∥ moderation      run together — both need only the turn (ADR-0003)
          │
          ▼
      (gate on the verdict — the barrier)
          │  proceed
          ▼
      provider discovery      read-only; may cross the barrier (see `turn.py`)
          │
          ▼
      planner agent           builds *and runs* the plan → `Evidence`
          │
          ▼
      response composer        `Evidence` → the prose a farmer reads
          │
          ▼
      TurnFinished

The understand phase (intent ∥ moderation ∥ discovery, and the barrier) is
delegated to `turn.py:run_turn`, which already owns that concurrency and is
tested there. This runner adds what the transport needs on top: the planner
and composer, the event stream, the evidence writes, and the mapping from a
turn's findings to a contract outcome.

Nothing side-effecting runs until moderation has cleared — the planner is
reached only on `PROCEED`, and its own `select` tool waits on the `Verdict`
before it touches a provider.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass

from dss.core.channel.models import ComposedAnswer
from dss.core.channel.service import answer_from_evidence, no_match_answer
from dss.core.intent.models import Intent
from dss.core.moderation.messages import messages_for
from dss.core.moderation.models import ModerationDecision, Outcome
from dss.core.planner.models import Evidence, Verdict
from dss.core.planner.sufficiency import unserved_asks
from dss.core.policy.models import Policy
from dss.core.provider_discovery.models import DiscoveryResult
from dss.core.shared.models import (
    Cause,
    Claim,
    RefusalBlock,
    TurnContext,
    TurnEvent,
    TurnFinished,
    TurnOutcome,
    TurnStarted,
    TurnStatus,
    UserTurn,
)
from dss.orchestration.compose import Compose
from dss.orchestration.discovery import DiscoverProviders
from dss.orchestration.plan import Plan
from dss.orchestration.turn import run_turn
from dss.ports.llm import LLMProvider
from dss.ports.sinks import TelemetrySink, TurnSink

# A moderation outcome is not a turn status: `CLARIFY` means the DSS understood
# and needs more from the farmer, which the contract calls `requires_input`.
_STATUS_FOR = {
    Outcome.REJECT: TurnStatus.REJECTED,
    Outcome.CLARIFY: TurnStatus.REQUIRES_INPUT,
    Outcome.NO_MATCH: TurnStatus.NO_MATCH,
}

# STUB(#86): no component reports confidence yet. The contract requires the
# field, so the runner supplies a number per status. Note these are not
# comparable — a refusal's certainty and an answer's certainty measure
# different things, which is the open question behind the field.
_STUB_CONFIDENCE = {
    TurnStatus.ANSWERED: 92,
    TurnStatus.PARTIALLY_ANSWERED: 74,
    TurnStatus.REJECTED: 98,
    TurnStatus.NO_MATCH: 88,
    TurnStatus.REQUIRES_INPUT: 80,
    TurnStatus.UNAVAILABLE: 0,
}


def outcome_for(status: TurnStatus, cause: Cause | None = None) -> TurnOutcome:
    return TurnOutcome(status=status, cause=cause, confidence=_STUB_CONFIDENCE[status])


@dataclass(frozen=True)
class Components:
    """The real components a turn runs through downstream of the understand
    phase, each already bound to its model, ports and config in the composition
    root. Swapping an implementation is a change there, not here.

    `discover` chains off intent inside `run_turn`; `plan` and `compose` run
    here, past the barrier."""

    discover: DiscoverProviders
    plan: Plan
    compose: Compose


class Orchestrator:
    """Satisfies `ports.turn.TurnRunner`. The live runner: real intent,
    moderation, discovery, planner and composer, streamed to the transport."""

    def __init__(
        self,
        *,
        intent_llm: LLMProvider,
        moderation_llm: LLMProvider,
        policies: Sequence[Policy],
        components: Components,
        turns: TurnSink,
        telemetry: TelemetrySink,
    ) -> None:
        self._intent_llm = intent_llm
        self._moderation_llm = moderation_llm
        self._policies = policies
        self._components = components
        self._turns = turns
        self._telemetry = telemetry

    async def run(self, turn: UserTurn, ctx: TurnContext) -> AsyncIterator[TurnEvent]:
        yield TurnStarted()
        self._turns.opened(ctx, turn)

        # Intent ∥ moderation, discovery chained off intent, and the barrier —
        # all owned by `run_turn`. It blanks intent and discovery on any
        # non-PROCEED outcome, so a refused turn surfaces neither.
        result = await run_turn(
            turn,
            intent_llm=self._intent_llm,
            moderation_llm=self._moderation_llm,
            policies=self._policies,
            discover_providers=self._components.discover,
        )
        decision = result.decision
        self._note("moderation", ctx, decision.outcome.value)

        if decision.outcome is not Outcome.PROCEED:
            yield self._finish(ctx, _refused(decision))
            return

        self._note("intent", ctx, _classified(result.intent))

        # Nobody can serve the ask: no provider is reachable for it, so there is
        # nothing for the planner to call and nothing for the composer to write
        # from. Answer NO_MATCH here rather than spend a planner and composer
        # round-trip to arrive at the same empty-handed place.
        if _nobody_serves(result.discovery):
            yield self._finish(
                ctx, (outcome_for(TurnStatus.NO_MATCH), no_match_answer())
            )
            return

        # Past the barrier: the decision cleared, so the planner's `select` tool
        # is free to call providers. The `Verdict` is how that clearance reaches
        # the tool, which may be several model round-trips deep.
        verdict = Verdict()
        verdict.set(decision)
        evidence = await self._components.plan(
            turn, intent=result.intent, discovery=result.discovery, verdict=verdict
        )

        text = await self._components.compose(evidence, turn=turn)
        answer = answer_from_evidence(text, evidence)
        for block in answer.content:
            yield Claim(content=block)
        self._note("channel", ctx, str(len(answer.content)))

        status, cause = _status_for(evidence, result.intent)
        yield self._finish(ctx, (outcome_for(status, cause), answer))

    def _finish(
        self, ctx: TurnContext, resolved: tuple[TurnOutcome, ComposedAnswer]
    ) -> TurnFinished:
        """Build the terminal event and record it.

        The turn sink is required, so a failure here is deliberately not caught —
        answering while silently failing to record the turn is not a success
        worth having.
        """

        outcome, answer = resolved
        finished = TurnFinished(
            outcome=outcome, content=answer.content, sources=answer.sources
        )
        self._turns.closed(ctx, finished)
        return finished

    def _note(self, stage: str, ctx: TurnContext, outcome: str) -> None:
        """Telemetry is optional (`ports/sinks.py`), so losing a span must never
        cost the farmer an answer."""

        try:
            self._telemetry.stage(stage, ctx, outcome)
        except Exception:  # noqa: BLE001 - an optional sink may fail any way it likes
            pass


def _nobody_serves(discovery: DiscoveryResult) -> bool:
    """Whether discovery found anyone for any ask. An empty dict, or one whose
    values are all empty tuples, both mean nobody: a Direct answer lands in
    `answers`, an OnDemand capability in `capabilities`, and neither being
    present for any ask is the no-match the composer would otherwise be asked to
    narrate from nothing."""

    return not any(discovery.answers.values()) and not any(
        discovery.capabilities.values()
    )


def _status_for(evidence: Evidence, intent: Intent) -> tuple[TurnStatus, Cause | None]:
    """Map what the loop actually gathered to a contract outcome. Reads facts
    off `Evidence` — it invents no rule, so no business `if` escapes `core/`:

    - no results, but calls failed → the providers exist but could not be
      reached: `unavailable`, which the transport reports as a retryable error.
    - no results, no failures → nobody served it after all: `no_match`.
    - some asks unserved → `partially_answered` (design v2 §6.2).
    - every ask served → `answered`.
    """

    if not evidence.results:
        if evidence.failed:
            return TurnStatus.UNAVAILABLE, Cause.PROVIDER_UNAVAILABLE
        return TurnStatus.NO_MATCH, None
    if unserved_asks(evidence, intent=intent):
        return TurnStatus.PARTIALLY_ANSWERED, None
    return TurnStatus.ANSWERED, None


def _classified(intent: Intent) -> str:
    """A non-personal summary for telemetry: which categories the turn asked
    about, never the question itself."""

    return (
        ",".join(ask.subject_categories.value for ask in intent.asks) or "unclassified"
    )


def _refused(decision: ModerationDecision) -> tuple[TurnOutcome, ComposedAnswer]:
    """Turn a moderation decision into what the farmer reads.

    The wording comes from `core/moderation/messages.py` rather than from here —
    the runner does not write prose. A `NO_MATCH` decision falls back to the
    composer's own no-match answer when moderation supplies no message.
    """

    status = _STATUS_FOR[decision.outcome]
    cause = Cause(decision.reason_code.value) if decision.reason_code else None
    texts = messages_for(decision)

    if not texts:
        return outcome_for(status, cause), no_match_answer()
    return outcome_for(status, cause), ComposedAnswer(
        content=tuple(RefusalBlock(text=text) for text in texts)
    )
