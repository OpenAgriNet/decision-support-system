"""The runner — the spine.

It owns exactly one thing: **the order**. Which stage runs when, and which
terminal each early exit takes. Every branch below reads a value another module
decided; none of them encodes a rule. The moment one grows
`if turn.channel == "voice" and len(text) > 200`, a rule has escaped `core/`.

Streaming is not this module's business either. It yields events as they are
produced; whether they become SSE frames or one JSON body is decided by the
transport, which is why `run` returns an iterator rather than taking a sink to
push into.

Sequential for now — ADR-0001 makes `pydantic-graph` the declared escalation,
not the starting point, and this flow is a sequence with early-exit terminals.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from dss.core.channel.models import ComposedAnswer
from dss.core.channel.service import compose, no_match_answer
from dss.core.intent.service import recognise_intent
from dss.core.moderation.models import Outcome
from dss.core.moderation.service import screen
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
from dss.ports.llm import LLM
from dss.ports.sinks import TelemetrySink, TurnSink


class CoreRunner:
    def __init__(self, *, llm: LLM, turns: TurnSink, telemetry: TelemetrySink) -> None:
        self._llm = llm
        self._turns = turns
        self._telemetry = telemetry

    async def run(self, turn: UserTurn, ctx: TurnContext) -> AsyncIterator[TurnEvent]:
        yield TurnStarted()
        self._turns.opened(ctx, turn)

        screening = await screen(turn, llm=self._llm)
        self._note("moderation", ctx, screening.outcome.value)
        if screening.outcome is not Outcome.PROCEED:
            yield self._finish(
                ctx,
                TurnOutcome(status=TurnStatus.REJECTED, cause=screening.cause),
                ComposedAnswer(content=(RefusalBlock(text=screening.message),)),
            )
            return

        intent = await recognise_intent(turn, llm=self._llm)
        self._note("intent", ctx, intent.primary_domain if intent else "unclassified")
        if intent is None:
            yield self._finish(
                ctx,
                TurnOutcome(
                    status=TurnStatus.NO_MATCH, cause=Cause.INTENT_LOW_CONFIDENCE
                ),
                no_match_answer(turn),
            )
            return

        answer = await compose(turn, intent, llm=self._llm)
        for block in answer.content:
            yield Claim(content=block)
        self._note("channel", ctx, str(len(answer.content)))

        yield self._finish(ctx, TurnOutcome(status=TurnStatus.ANSWERED), answer)

    def _finish(
        self, ctx: TurnContext, outcome: TurnOutcome, answer: ComposedAnswer
    ) -> TurnFinished:
        """Build the terminal event and record it.

        The turn sink is required, so a failure here is deliberately *not*
        caught — answering while silently failing to record the turn is not a
        success worth having.
        """

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
