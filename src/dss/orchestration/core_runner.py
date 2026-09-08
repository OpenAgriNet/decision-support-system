"""The turn runner — it owns the order, and nothing else.

Sequencing is delegated where main already established it: `run_turn` classifies
intent and moderates **in parallel** (ADR-0003), and gates the result — a turn
that will not proceed surfaces no classification. This runner adds what the
transport needs on top: the event stream, the evidence writes, and the mapping
from a moderation decision to a turn outcome.

Every branch below reads a value another module decided. The runner contains no
`if` with business meaning — the moment it grows one, a rule has escaped `core/`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from dss.core.channel.models import ComposedAnswer
from dss.core.channel.service import compose, no_match_answer
from dss.core.intent.models import Intent
from dss.core.moderation.messages import messages_for
from dss.core.moderation.models import ModerationDecision, Outcome
from dss.core.policy.models import Policy
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
from dss.orchestration.discovery import DiscoverProviders
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
# comparable — a refusal's certainty and an answer's certainty measure different
# things, which is the open question behind the field.
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


class CoreRunner:
    """Satisfies `ports.turn.TurnRunner`."""

    def __init__(
        self,
        *,
        intent_llm: LLMProvider,
        moderation_llm: LLMProvider,
        policies: Sequence[Policy],
        discover_providers: DiscoverProviders,
        turns: TurnSink,
        telemetry: TelemetrySink,
    ) -> None:
        self._intent_llm = intent_llm
        self._moderation_llm = moderation_llm
        self._policies = policies
        self._discover_providers = discover_providers
        self._turns = turns
        self._telemetry = telemetry

    async def run(self, turn: UserTurn, ctx: TurnContext) -> AsyncIterator[TurnEvent]:
        yield TurnStarted()
        self._turns.opened(ctx, turn)

        result = await run_turn(
            turn,
            intent_llm=self._intent_llm,
            moderation_llm=self._moderation_llm,
            policies=self._policies,
            discover_providers=self._discover_providers,
        )
        decision = result.decision
        self._note("moderation", ctx, decision.outcome.value)

        if decision.outcome is not Outcome.PROCEED:
            yield self._finish(ctx, _refused(decision))
            return

        self._note("intent", ctx, _classified(result.intent))

        answer = await compose(turn, result.intent, llm=self._intent_llm)
        for block in answer.content:
            yield Claim(content=block)
        self._note("channel", ctx, str(len(answer.content)))

        yield self._finish(ctx, (outcome_for(TurnStatus.ANSWERED), answer))

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
