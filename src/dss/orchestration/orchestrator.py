"""The orchestrator — the live turn runner (design doc §4).

It holds the turn and calls each real component in order, then streams the
result to the transport. Components never call each other; every one takes
plain objects and returns plain objects, and this module is the only place
that knows the sequence. Order is code, not config.

    redaction                first — every stage below sees tags (ADR-0015)
          │
          ▼
    translate in             only for a language with a route (#174); every
          │                  stage below reads the route's language
          ▼
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

`Orchestrator.run` opens the turn's span and hands the turn to the path for
its input kind. `TextPath` is the path for a sentence, in three layers: `run`
is the edge (redact, record, reveal kept values),
`orchestration/translation.py` translates in and out or passes through, and
`_run_core` runs the pipeline on tags only.

Nothing side-effecting runs until moderation has cleared — the planner is
reached only on `PROCEED`, and its own `select` tool waits on the `Verdict`
before it touches a provider.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Iterable, Mapping, Sequence
from contextlib import aclosing, suppress
from dataclasses import dataclass
from typing import Any, Protocol

from dss.adapters.observability.tracing import (
    TurnRecorder,
    set_current_span_content,
    turn_span,
)
from dss.core.channel.localize import LocalizeText, localize_answer
from dss.core.channel.models import ClarificationText, ComposedAnswer
from dss.core.channel.service import (
    answer_for_unplaced_asks,
    answer_for_unserved_asks,
    answer_from_evidence,
    no_match_answer,
    question_for_unplaced_asks,
)
from dss.core.intent.models import Intent
from dss.core.moderation.messages import messages_for
from dss.core.moderation.models import ModerationDecision, Outcome
from dss.core.planner.models import Evidence, Verdict
from dss.core.planner.place import place_optional_asks
from dss.core.planner.validation import DomainSchema
from dss.core.policy.models import Policy
from dss.core.provider_discovery.models import DiscoveryResult
from dss.core.redaction.turn import texts_of, with_texts
from dss.core.redaction.visibility import StreamReveal, Visibility
from dss.core.shared.models import (
    Cause,
    Claim,
    ClaimDelta,
    OutputBlock,
    RefusalBlock,
    TextBlock,
    TextQuery,
    TurnContext,
    TurnEvent,
    TurnFinished,
    TurnInput,
    TurnOutcome,
    TurnStarted,
    TurnStatus,
    UserTurn,
)
from dss.core.stream_response.citations import CitationNumbers
from dss.core.stream_response.service import ComposeStream
from dss.core.translation.service import EdgeTranslation
from dss.observability.stages import Stage
from dss.observability.trace_log import bind_turn_ids, trace_component
from dss.orchestration.answer_stream import AnswerStream
from dss.orchestration.discovery import DiscoverProviders
from dss.orchestration.events import CoreEvent
from dss.orchestration.plan import Plan
from dss.orchestration.redaction import RedactTexts
from dss.orchestration.stage_content import (
    asks_text,
    cited_sources_text,
    evidence_text,
)
from dss.orchestration.translation import translate_in, translate_out
from dss.orchestration.turn import run_turn
from dss.ports.area_lookup import AreaLookup
from dss.ports.llm import LLMProvider
from dss.ports.prompts import PromptProvider
from dss.ports.scheme_catalog import SchemeCatalog
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

    `redact` runs first, before anything else sees the turn. `discover` chains
    off intent inside `run_turn`; `plan` and `compose` run here, past the
    barrier.

    `compose` streams. There is no whole-answer variant: a caller who wants one
    body gets it by draining, which the transport already does. That keeps the
    runner ignorant of how its events are delivered — `ports/turn.py` keeps the
    transport's mode on the transport's side of the seam.

    `localize` renders system-authored text — refusals, clarification
    questions, the no-provider answers — in the turn's target language
    (ADR-0018). It is a no-op for English and falls back to the English text
    on any failure, so every call site below may lean on it unconditionally."""

    redact: RedactTexts
    discover: DiscoverProviders
    plan: Plan
    compose: ComposeStream
    localize: LocalizeText


class TurnPath[InputT: TurnInput](Protocol):
    """One input kind's pipeline, from the turn as sent to the events the
    farmer reads."""

    def run(
        self, turn: UserTurn[InputT], *, ctx: TurnContext, recorder: TurnRecorder
    ) -> AsyncGenerator[TurnEvent]: ...


# `Any`: the table mixes kinds, and the type checker cannot say that a value's
# kind equals its key. The boot check does.
type TurnPaths = Mapping[type[TurnInput], TurnPath[Any]]


class Orchestrator:
    """Satisfies `ports.turn.TurnRunner`. Opens the turn's span, then runs the
    path wired for the input's kind."""

    def __init__(self, *, paths: TurnPaths) -> None:
        self._paths = paths

    async def run(
        self, turn: UserTurn[TurnInput], ctx: TurnContext
    ) -> AsyncIterator[TurnEvent]:
        bind_turn_ids(
            ctx.trace_id, message_id=ctx.message_id, session_id=ctx.session_id
        )
        # Opened around the whole turn so every span Pydantic AI makes inside it
        # — intent, moderation, planner, composer — lands under one trace with
        # the ids on its root. `run` is an async generator, so the span stays
        # open across the yields and closes when the consumer is done with it.
        with turn_span(
            trace_id=ctx.trace_id,
            message_id=ctx.message_id,
            session_id=ctx.session_id,
        ) as recorder:
            yield TurnStarted()
            path = self._paths[type(turn.input)]
            # `aclosing`: closing this generator does not reach the path's, and
            # a closed screen must close every model stream.
            async with aclosing(path.run(turn, ctx=ctx, recorder=recorder)) as events:
                async for event in events:
                    yield event


def unrouted_kinds(
    paths: TurnPaths, kinds: Iterable[type[TurnInput]]
) -> list[type[TurnInput]]:
    return [kind for kind in kinds if kind not in paths]


class TextPath:
    """The path for a sentence: real intent, moderation, discovery, planner and
    composer, streamed to the transport."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        intent_llm: LLMProvider,
        moderation_llm: LLMProvider,
        prompts: PromptProvider,
        policies: Sequence[Policy],
        scheme_catalog: SchemeCatalog,
        scheme_fuzzy_threshold: float | None,
        nearest_max_km: float,
        components: Components,
        turns: TurnSink,
        telemetry: TelemetrySink,
        area_lookup: AreaLookup,
        clarification_text: ClarificationText,
        schemas: Mapping[str, DomainSchema],
        edge_translation: EdgeTranslation,
    ) -> None:
        self._intent_llm = intent_llm
        self._moderation_llm = moderation_llm
        self._prompts = prompts
        self._policies = policies
        # Required, not optional. An unmounted catalog is already
        # representable as an empty one, so defaulting here would make a
        # wiring mistake and a missing mount look identical.
        self._scheme_catalog = scheme_catalog
        self._scheme_fuzzy_threshold = scheme_fuzzy_threshold
        self._nearest_max_km = nearest_max_km
        self._components = components
        self._turns = turns
        self._telemetry = telemetry
        self._area_lookup = area_lookup
        self._clarification_text = clarification_text
        # The same schemas the planner validates against. Read here for one
        # thing only: whether a pack serves an ask that names no place.
        self._schemas = schemas
        self._edge_translation = edge_translation

    async def run(
        self, turn: UserTurn[TextQuery], *, ctx: TurnContext, recorder: TurnRecorder
    ) -> AsyncGenerator[TurnEvent]:
        with trace_component(Stage.REDACTION, ctx.trace_id):
            redaction = await self._components.redact(
                texts_of(turn), request_id=turn.transaction_id
            )
        farmer_turn = with_texts(turn, redaction.texts)
        recorder.redacted(found=redaction.found, failed=redaction.failed)
        self._turns.opened(ctx, farmer_turn)

        translation = self._edge_translation.for_turn(farmer_turn)
        core_turn = await translate_in(farmer_turn, translation, trace_id=ctx.trace_id)

        # `aclosing` each layer: closing an async generator does not reach the
        # one it reads from, and a closed screen must close every model stream.
        async with (
            aclosing(
                self._run_core(
                    core_turn,
                    ctx=ctx,
                    recorder=recorder,
                    visibility=redaction.visibility,
                )
            ) as core_events,
            aclosing(
                translate_out(core_events, translation, trace_id=ctx.trace_id)
            ) as translated_events,
            aclosing(
                self._reveal_to_farmer(
                    translated_events,
                    ctx=ctx,
                    farmer_turn=farmer_turn,
                    recorder=recorder,
                    visibility=redaction.visibility,
                )
            ) as farmer_events,
        ):
            async for event in farmer_events:
                yield event

    async def _run_core(
        self,
        turn: UserTurn[TextQuery],
        *,
        ctx: TurnContext,
        recorder: TurnRecorder,
        visibility: Visibility,
    ) -> AsyncGenerator[CoreEvent]:
        """The pipeline on a redacted turn. It yields tags only; `visibility` is
        for the planner's provider calls.
        """

        # Intent ∥ moderation, discovery chained off intent, and the barrier —
        # all owned by `run_turn`. It blanks intent and discovery on any
        # non-PROCEED outcome, so a refused turn surfaces neither.
        result = await run_turn(
            turn,
            intent_llm=self._intent_llm,
            moderation_llm=self._moderation_llm,
            prompts=self._prompts,
            policies=self._policies,
            discover_providers=self._components.discover,
            area_lookup=self._area_lookup,
            scheme_catalog=self._scheme_catalog,
            scheme_fuzzy_threshold=self._scheme_fuzzy_threshold,
            nearest_max_km=self._nearest_max_km,
        )
        decision = result.decision
        self._note("moderation", ctx, decision.outcome.value)

        if decision.outcome is not Outcome.PROCEED:
            outcome, refusal = _refused(decision)
            refusal = await localize_answer(
                refusal, self._components.localize, turn=turn
            )
            yield _build_turn_finished(outcome, refusal)
            return

        self._note("intent", ctx, _classified(result.intent))
        # After the moderation gate, so a refused turn's asks are not
        # counted as something farmers asked for.
        asks = result.intent.asks
        recorder.asked(
            categories=[ask.subject_categories.value for ask in asks],
            interactions=[ask.interaction_type.value for ask in asks],
            subjects=[ask.agriculture_subjects or "" for ask in asks],
        )

        # Nothing to ask about. Every reply below is built per ask, so
        # from zero asks it would be an empty bubble. Say so instead.
        if not asks:
            answer = await localize_answer(
                no_match_answer(), self._components.localize, turn=turn
            )
            yield _build_turn_finished(outcome_for(TurnStatus.NO_MATCH), answer)
            return

        # No ask has a place to search around: named nowhere, a name the
        # index does not carry, or a name matching several. Ask rather
        # than answer from nowhere or guess. If any ask has a place, the
        # turn goes on; the others reach the composer as failures.
        #
        # An ask naming nowhere is not always missing a place: the pack
        # decides (`core/planner/place.py`), which is why this needs
        # discovery and runs after it rather than inside `run_turn`.
        # Discovery is read-only and already allowed to be wasted (it
        # starts before moderation has cleared the turn), so letting it
        # run and discarding it costs one cheap call.
        place_optional = place_optional_asks(
            result.intent, result.discovery, self._schemas
        )
        clarification = answer_for_unplaced_asks(
            result.intent.asks,
            self._clarification_text,
            place_optional=place_optional,
        )
        if clarification is not None:
            clarification = await localize_answer(
                clarification, self._components.localize, turn=turn
            )
            yield _build_turn_finished(
                outcome_for(TurnStatus.REQUIRES_INPUT), clarification
            )
            return

        # Nobody can serve the ask: no provider is reachable for it, so there is
        # nothing for the planner to call and nothing for the composer to write
        # from. Answer NO_MATCH here rather than spend a planner and composer
        # round-trip to arrive at the same empty-handed place.
        if _nobody_serves(result.discovery):
            status = TurnStatus.NO_MATCH
            answer = answer_for_unserved_asks(
                result.intent.asks, self._clarification_text
            )
            # An ask whose place matched several was never searched. Still
            # ask which one, so the farmer's reply can finish it.
            question = question_for_unplaced_asks(
                result.intent.asks,
                self._clarification_text,
                place_optional=place_optional,
            )
            if question is not None:
                status = TurnStatus.REQUIRES_INPUT
                answer = ComposedAnswer(
                    content=(*answer.content, TextBlock(text=question))
                )
            answer = await localize_answer(answer, self._components.localize, turn=turn)
            yield _build_turn_finished(outcome_for(status), answer)
            return

        # Past the barrier: the decision cleared, so the planner's `select` tool
        # is free to call providers. The `Verdict` is how that clearance reaches
        # the tool, which may be several model round-trips deep.
        verdict = Verdict()
        verdict.set(decision)
        with trace_component(Stage.PLANNER, ctx.trace_id):
            evidence = await self._components.plan(
                turn,
                intent=result.intent,
                discovery=result.discovery,
                verdict=verdict,
                visibility=visibility,
            )
            set_current_span_content(
                input=asks_text(result.intent.asks),
                output=evidence_text(evidence),
            )

        # The span stays open across the yields below, so it measures the
        # whole composition rather than closing on the first piece.
        with trace_component(Stage.COMPOSER, ctx.trace_id):
            answer_stream = AnswerStream()
            numbers = CitationNumbers(source.id for source in evidence.sources)
            # `aclosing`, not a bare `async for`: a farmer who closes the
            # screen mid-answer must close the model's stream too, and
            # closing an async generator does not reach the one it relays
            # from.
            async with (
                aclosing(self._components.compose(evidence, turn=turn)) as written,
                aclosing(numbers.renumber(written)) as pieces,
            ):
                async for piece in pieces:
                    yield answer_stream.add_piece(piece)
            # A failure before this line propagates: the pieces already
            # yielded cannot be recalled, so there is no retry to make and
            # nothing to roll back. The transport reports a failed turn.
            # An ask still missing its place was not answered: a name that
            # matched several, or nowhere named where the pack needs one.
            # Ask as the last piece of the same answer, so it is one
            # bubble and the farmer's next reply can finish it.
            question = question_for_unplaced_asks(
                result.intent.asks,
                self._clarification_text,
                place_optional=place_optional,
            )
            if question is not None:
                question = await self._components.localize(question, turn=turn)
                yield answer_stream.add_piece(numbers.feed(f"\n\n{question}"))

            set_current_span_content(
                input=cited_sources_text(evidence, numbers.cited),
                output=numbers.composed,
            )

        answer = answer_from_evidence(numbers.composed, evidence)
        recorder.composed()
        # One block: the composer's prose is never split (`answer_from_evidence`).
        (block,) = answer.content
        yield answer_stream.claim(block, answer.sources)

        status, cause = _status_for(evidence)
        yield answer_stream.finish(outcome_for(status, cause))

    async def _reveal_to_farmer(
        self,
        events: AsyncIterator[CoreEvent],
        *,
        ctx: TurnContext,
        farmer_turn: UserTurn[TextQuery],
        recorder: TurnRecorder,
        visibility: Visibility,
    ) -> AsyncGenerator[TurnEvent]:
        """The turn's events as the farmer reads them, with their own values
        in place of the tags. The record and the trace keep the tags."""

        shown = StreamReveal(visibility)
        answer_stream = AnswerStream()
        has_first_delta = False
        async for event in events:
            match event:
                case ClaimDelta(text=text):
                    if not has_first_delta:
                        recorder.first_delta()
                        has_first_delta = True
                    if piece := shown.feed(text):
                        yield answer_stream.add_piece(piece)
                case Claim(content=block, sources=sources):
                    if rest := shown.flush():
                        yield answer_stream.add_piece(rest)
                    yield answer_stream.claim(block, sources)
                case TurnFinished():
                    self._record_finished(
                        event, ctx=ctx, farmer_turn=farmer_turn, recorder=recorder
                    )
                    # A streamed reply ends on what the farmer was streamed; a
                    # fixed one reaches the edge whole, with no claims before it.
                    yield (
                        answer_stream.finish(event.outcome)
                        if answer_stream.has_claims
                        else _reveal_finished(event, visibility)
                    )

    def _record_finished(
        self,
        finished: TurnFinished,
        *,
        ctx: TurnContext,
        farmer_turn: UserTurn[TextQuery],
        recorder: TurnRecorder,
    ) -> None:
        """Record the terminal event, with its tags.

        The turn sink is required, so a failure here is deliberately not caught —
        answering while silently failing to record the turn is not a success
        worth having.

        Every way out of the core ends in a `TurnFinished`, which is why the
        span's `status` is set here rather than at each of them. It is set
        after the turn is recorded, so a turn whose record failed stays
        ``error``.
        """

        self._turns.closed(ctx, finished)
        recorder.status(finished.outcome.status.value)
        # The trace gets the redacted question and the tagged answer.
        recorder.content(
            query=farmer_turn.input.text, answer=_spoken_text(finished.content)
        )

    def _note(self, stage: str, ctx: TurnContext, outcome: str) -> None:
        """Telemetry is optional (`ports/sinks.py`), so losing a span must never
        cost the farmer an answer."""

        # An optional sink may fail any way it likes.
        with suppress(Exception):
            self._telemetry.stage(stage, ctx, outcome)


def _build_turn_finished(outcome: TurnOutcome, answer: ComposedAnswer) -> TurnFinished:
    return TurnFinished(outcome=outcome, content=answer.content, sources=answer.sources)


def _reveal_finished(finished: TurnFinished, visibility: Visibility) -> TurnFinished:
    if not visibility.values:
        return finished
    return finished.model_copy(
        update={
            "content": tuple(_revealed(block, visibility) for block in finished.content)
        }
    )


def _spoken_text(blocks: Sequence[OutputBlock]) -> list[str]:
    """What the farmer reads. A block with no words, such as a form, adds none."""

    return [
        block.text for block in blocks if isinstance(block, TextBlock | RefusalBlock)
    ]


def _revealed(block: OutputBlock, visibility: Visibility) -> OutputBlock:
    """Only words can hold a tag, so only a block with words changes."""

    match block:
        case TextBlock() | RefusalBlock():
            return block.model_copy(update={"text": visibility.reveal(block.text)})
    return block


def _nobody_serves(discovery: DiscoveryResult) -> bool:
    """Whether discovery found anyone for any ask. An empty dict, or one whose
    values are all empty tuples, both mean nobody: a Direct answer lands in
    `answers`, an OnDemand capability in `capabilities`, and neither being
    present for any ask is the no-match the composer would otherwise be asked to
    narrate from nothing."""

    return not any(discovery.answers.values()) and not any(
        discovery.capabilities.values()
    )


def _status_for(evidence: Evidence) -> tuple[TurnStatus, Cause | None]:
    """Map what the loop actually gathered to a contract outcome. Reads facts
    off `Evidence` — it invents no rule, so no business `if` escapes `core/`:

    - no results, but provider calls failed → the providers exist but could
      not be reached: `unavailable`, which the transport reports as a
      retryable error.
    - no results, no provider failures → nobody served it after all:
      `no_match`. A place failure called no provider, so it lands here.
    - some asks unserved → `partially_answered` (design v2 §6.2).
    - every ask served → `answered`.
    """

    if not evidence.has_any_result:
        if evidence.has_provider_failure:
            return TurnStatus.UNAVAILABLE, Cause.PROVIDER_UNAVAILABLE
        return TurnStatus.NO_MATCH, None
    if evidence.unserved_asks:
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
