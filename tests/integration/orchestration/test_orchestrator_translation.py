"""Tier 3 — translation between the edge and the core, with a `FakeTranslator`
that marks each text with the language it was sent to.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from dss.adapters.observability.tracing import open_span
from dss.adapters.sinks.memory import MemoryTurnSink
from dss.core.intent.models import (
    ClassifiedAsk,
    IntentClassification,
    InteractionType,
    SubjectCategory,
)
from dss.core.planner.models import Result
from dss.core.provider_discovery.models import DiscoveryResult
from dss.core.shared.errors import TranslationError
from dss.core.shared.language import ENGLISH, LanguageTag
from dss.core.shared.models import (
    Claim,
    ClaimDelta,
    Geometry,
    Location,
    RefusalBlock,
    TextQuery,
    TurnEvent,
    TurnFinished,
    TurnStatus,
    UserTurn,
)
from dss.core.translation.models import TranslationRoute
from dss.core.translation.service import EdgeTranslation
from dss.observability.trace_log import set_stage_span_opener
from dss.orchestration.orchestrator import Orchestrator, outcome_for
from dss.orchestration.plan import Plan
from dss.orchestration.redaction import RedactTexts, pass_through
from dss.orchestration.translation import translate_out
from dss.ports.area_lookup import AreaMatch
from tests.support.fakes import FakeAreaLookup, FakeLocalizer, FakeTranslator
from tests.support.turn_events import assert_well_formed

from .test_orchestrator import (
    _ANSWERED_EVIDENCE,
    _PUNE,
    _PUNE_MATCH,
    DELETE_COMMAND,
    _build,
    _ctx,
    _evidence,
    _FakeCompose,
    _FakePlan,
    _one_ask,
    _served_discovery,
)
from .test_orchestrator_redaction import (
    PHONE,
    _FindsValues,
    _RecordingIntentLLM,
    _RecordingModerationLLM,
    _redact,
)

GUJARATI = LanguageTag("gu")
MARATHI = LanguageTag("mr")
BHILI = LanguageTag("bhb")
QUERY = "આજે વરસાદ પડશે?"
ANSWER = "It will rain today [1]."
NOBODY = DiscoveryResult(answers={}, capabilities={}, failures={}, events=())


def _build_turn(
    lang: LanguageTag, query: str = QUERY, *, location: Location | None = _PUNE
) -> UserTurn:
    return UserTurn(
        input=TextQuery(text=query),
        session_id="s1",
        transaction_id="t1",
        source_lang=lang,
        target_lang=lang,
        channel="web",
        location=location,
    )


@dataclass(frozen=True)
class _Translating:
    orchestrator: Orchestrator
    turns: MemoryTurnSink


def _build_translating(  # noqa: PLR0913
    translator: FakeTranslator,
    *,
    language: str = "gu",
    to: LanguageTag = ENGLISH,
    intent: IntentClassification | None = None,
    discovery: DiscoveryResult | None = None,
    plan: Plan | None = None,
    compose: _FakeCompose | None = None,
    violated: str | None = None,
    area_lookup: FakeAreaLookup | None = None,
    localize: FakeLocalizer | None = None,
    redact: RedactTexts = pass_through,
    intent_llm: _RecordingIntentLLM | None = None,
    moderation_llm: _RecordingModerationLLM | None = None,
) -> _Translating:
    """An orchestrator that translates `language` to `to` and, unless told
    otherwise, answers. One LLM policy, so moderation asks its model."""

    orchestrator, turns = _build(
        intent=intent or _one_ask(),
        discovery=discovery or _served_discovery(),
        plan=plan or _FakePlan(_ANSWERED_EVIDENCE),
        compose=compose or _FakeCompose(ANSWER),
        violated=violated,
        policies=(DELETE_COMMAND,),
        area_lookup=area_lookup,
        localize=localize,
        redact=redact,
        intent_llm=intent_llm,
        moderation_llm=moderation_llm,
        edge_translation=EdgeTranslation(
            routes={language: TranslationRoute(translator=translator, to=to)}
        ),
    )
    return _Translating(orchestrator=orchestrator, turns=turns)


async def _drain(orch: Orchestrator, turn: UserTurn) -> list[TurnEvent]:
    events = [event async for event in orch.run(turn, _ctx())]
    assert_well_formed(events)
    return events


async def _drain_until_it_raises(orch: Orchestrator, turn: UserTurn) -> list[TurnEvent]:
    """The events that arrived before `TranslationError`."""

    events: list[TurnEvent] = []
    with pytest.raises(TranslationError):
        async for event in orch.run(turn, _ctx()):
            events.append(event)
    return events


def _find_finished(events: Sequence[TurnEvent]) -> TurnFinished:
    (finished,) = [event for event in events if isinstance(event, TurnFinished)]
    return finished


def _collect_streamed_texts(events: Sequence[TurnEvent]) -> list[str]:
    return [event.text for event in events if isinstance(event, ClaimDelta)]


def _find_claims(events: Sequence[TurnEvent]) -> list[Claim]:
    return [event for event in events if isinstance(event, Claim)]


def _assert_streamed_block_by_block(events: Sequence[TurnEvent]) -> None:
    """Well formed, and the answer was streamed: every block was claimed."""

    assert_well_formed(events)
    assert _find_claims(events)


# --- in ----------------------------------------------------------------------


async def test_gu_turn_reaches_intent_and_moderation_in_english() -> None:
    intent_llm = _RecordingIntentLLM(_one_ask())
    moderation_llm = _RecordingModerationLLM()
    plan = _FakePlan(_ANSWERED_EVIDENCE)
    orch = _build_translating(
        FakeTranslator(),
        plan=plan,
        intent_llm=intent_llm,
        moderation_llm=moderation_llm,
    ).orchestrator

    await _drain(orch, _build_turn(GUJARATI))

    assert any(f"[en] {QUERY}" in prompt for prompt in intent_llm.seen)
    assert any(f"[en] {QUERY}" in prompt for prompt in moderation_llm.seen)
    assert plan.turn.target_lang == ENGLISH


async def test_inner_turn_runs_in_the_route_language() -> None:
    intent_llm = _RecordingIntentLLM(_one_ask())
    plan = _FakePlan(_ANSWERED_EVIDENCE)
    orch = _build_translating(
        FakeTranslator(), language="bhb", to=MARATHI, plan=plan, intent_llm=intent_llm
    ).orchestrator

    await _drain(orch, _build_turn(BHILI))

    assert any(f"[mr] {QUERY}" in prompt for prompt in intent_llm.seen)
    assert plan.turn.target_lang == MARATHI


async def test_translator_receives_redacted_texts() -> None:
    translator = FakeTranslator()
    orch = _build_translating(
        translator, redact=_redact(_FindsValues(phone=PHONE))
    ).orchestrator

    await _drain(orch, _build_turn(GUJARATI, f"{QUERY} {PHONE}"))

    (_, texts, _, _) = translator.calls[0]
    assert "«phone_1»" in texts[0]
    assert all(PHONE not in text for text in texts)


async def test_audit_record_keeps_the_original_language() -> None:
    translating = _build_translating(FakeTranslator())
    orch, turns = translating.orchestrator, translating.turns

    await _drain(orch, _build_turn(GUJARATI))

    recorded = turns.records["t1"].turn
    assert recorded.input.text == QUERY
    assert recorded.target_lang == GUJARATI


async def test_translate_in_failure_fails_the_turn() -> None:
    orch = _build_translating(FakeTranslator(fail_on="translate")).orchestrator

    events = await _drain_until_it_raises(orch, _build_turn(GUJARATI))

    assert not any(isinstance(event, ClaimDelta | Claim) for event in events)


# --- out: fixed replies --------------------------------------------------------


async def test_refused_gu_turn_is_translated() -> None:
    orch = _build_translating(FakeTranslator(), violated="delete-command").orchestrator

    events = await _drain(orch, _build_turn(GUJARATI))

    _assert_streamed_block_by_block(events)
    content = _find_finished(events).content
    assert all(block.text.startswith("[gu] ") for block in content)
    assert all(isinstance(block, RefusalBlock) for block in content)


async def test_clarifying_gu_turn_is_translated() -> None:
    orch = _build_translating(FakeTranslator()).orchestrator

    events = await _drain(orch, _build_turn(GUJARATI, location=None))

    _assert_streamed_block_by_block(events)
    assert all(
        block.text.startswith("[gu] ") for block in _find_finished(events).content
    )


async def test_no_provider_gu_turn_is_translated() -> None:
    orch = _build_translating(FakeTranslator(), discovery=NOBODY).orchestrator

    events = await _drain(orch, _build_turn(GUJARATI))

    _assert_streamed_block_by_block(events)
    assert all(
        block.text.startswith("[gu] ") for block in _find_finished(events).content
    )


async def test_a_translator_that_cannot_stream_sends_one_piece_per_block() -> None:
    orch = _build_translating(
        FakeTranslator(pieces=1), violated="delete-command"
    ).orchestrator

    events = await _drain(orch, _build_turn(GUJARATI))

    _assert_streamed_block_by_block(events)
    assert len(_collect_streamed_texts(events)) == len(_find_claims(events))


async def test_a_blank_block_is_not_sent_to_the_translator() -> None:
    translator = FakeTranslator()
    translation = EdgeTranslation(
        routes={"gu": TranslationRoute(translator=translator, to=ENGLISH)}
    ).for_turn(_build_turn(GUJARATI))
    finished = TurnFinished(
        outcome=outcome_for(TurnStatus.REJECTED),
        content=(
            RefusalBlock(text=""),
            RefusalBlock(text=" "),
            RefusalBlock(text="No."),
        ),
    )

    async def core_events():
        yield finished

    events = [
        event
        async for event in translate_out(core_events(), translation, trace_id="t1")
    ]

    assert [texts for _, texts, _, _ in translator.calls] == [("No.",)]
    assert [claim.content.text for claim in _find_claims(events)] == [
        "",
        " ",
        "[gu] No.",
    ]
    _assert_streamed_block_by_block(events)


@pytest.mark.parametrize("to", [ENGLISH, MARATHI], ids=str)
async def test_localizer_works_in_the_route_language(to: LanguageTag) -> None:
    localizer = FakeLocalizer()
    orch = _build_translating(
        FakeTranslator(), to=to, localize=localizer, violated="delete-command"
    ).orchestrator

    await _drain(orch, _build_turn(GUJARATI))

    assert localizer.calls
    assert all(lang == to for _, lang in localizer.calls)


async def test_fixed_reply_translation_failure_fails_the_turn() -> None:
    orch = _build_translating(
        FakeTranslator(fail_on="stream"), violated="delete-command"
    ).orchestrator

    events = await _drain_until_it_raises(orch, _build_turn(GUJARATI))

    assert not any(isinstance(event, TurnFinished) for event in events)


async def test_audit_record_closes_in_the_farmers_language() -> None:
    translating = _build_translating(FakeTranslator())
    orch, turns = translating.orchestrator, translating.turns

    await _drain(orch, _build_turn(GUJARATI))

    assert turns.records["t1"].finished.content[0].text == f"[gu] {ANSWER}"


# --- out: the streamed answer --------------------------------------------------


async def test_gu_answer_streams_translated_pieces_only() -> None:
    orch = _build_translating(
        FakeTranslator(),
        compose=_FakeCompose("", chunks=("It will ", "rain today [1].")),
    ).orchestrator

    events = await _drain(orch, _build_turn(GUJARATI))

    (claim,) = _find_claims(events)
    _assert_streamed_block_by_block(events)
    assert "".join(_collect_streamed_texts(events)) == f"[gu] {ANSWER}"
    assert claim.content.text == f"[gu] {ANSWER}"
    assert [block.text for block in _find_finished(events).content] == [
        f"[gu] {ANSWER}"
    ]


async def test_translated_answer_keeps_the_cited_sources() -> None:
    translated = _build_translating(FakeTranslator()).orchestrator
    untranslated = _build_translating(FakeTranslator()).orchestrator

    in_gujarati = _find_finished(await _drain(translated, _build_turn(GUJARATI)))
    in_english = _find_finished(await _drain(untranslated, _build_turn(ENGLISH)))

    assert in_gujarati.sources
    assert in_gujarati.sources == in_english.sources
    assert in_gujarati.content[0].source_ids == in_english.content[0].source_ids


async def test_translator_sees_tags_farmer_sees_values() -> None:
    translator = FakeTranslator()
    translating = _build_translating(
        translator,
        redact=_redact(_FindsValues(phone=PHONE)),
        compose=_FakeCompose("We will message «phone_1» today [1]."),
    )
    orch, turns = translating.orchestrator, translating.turns

    events = await _drain(orch, _build_turn(GUJARATI, f"{QUERY} {PHONE}"))

    (streamed_text,) = [
        texts[0] for method, texts, _, _ in translator.calls if method == "stream"
    ]
    assert "«phone_1»" in streamed_text
    assert all(
        PHONE not in text for _, texts, _, _ in translator.calls for text in texts
    )
    assert (
        "".join(_collect_streamed_texts(events))
        == f"[gu] We will message {PHONE} today [1]."
    )
    assert PHONE in _find_finished(events).content[0].text
    assert "«phone_1»" in turns.records["t1"].finished.content[0].text


async def test_ambiguous_place_question_is_translated_with_the_answer() -> None:
    def weather_in(place_name: str) -> ClassifiedAsk:
        return ClassifiedAsk(
            subject_categories=SubjectCategory.WEATHER,
            interaction_type=InteractionType.OBSERVE,
            place_name=place_name,
        )

    def aurangabad(state: str) -> AreaMatch:
        return AreaMatch(
            name="Aurangabad",
            region="IN-XX",
            within=("India", state),
            geometry=Geometry(coordinates=[75.3, 19.9]),
        )

    orch = _build_translating(
        FakeTranslator(),
        intent=IntentClassification(
            asks=(weather_in("Pune"), weather_in("Aurangabad")), confidence=0.9
        ),
        plan=_FakePlan(
            _evidence(
                results=(Result(ask_index=0, source_id="1", data={"rain": "none"}),),
                served=(0,),
            )
        ),
        compose=_FakeCompose("No rain in Pune [1]."),
        area_lookup=FakeAreaLookup(
            {
                "pune": [_PUNE_MATCH],
                "aurangabad": [aurangabad("Maharashtra"), aurangabad("Bihar")],
            }
        ),
    ).orchestrator

    events = await _drain(orch, _build_turn(GUJARATI, location=None))

    streamed = "".join(_collect_streamed_texts(events))
    assert streamed.startswith("[gu] No rain in Pune [1].")
    assert streamed.endswith(
        "Which Aurangabad?\n1. Aurangabad, Maharashtra\n2. Aurangabad, Bihar"
    )


async def test_stream_failure_mid_way_raises_after_sent_pieces() -> None:
    orch = _build_translating(FakeTranslator(fail_on="stream")).orchestrator

    events = await _drain_until_it_raises(orch, _build_turn(GUJARATI))

    assert _collect_streamed_texts(events)
    assert not any(isinstance(event, TurnFinished) for event in events)


async def test_closing_mid_translation_closes_the_translator_stream() -> None:
    translator = FakeTranslator()
    orch = _build_translating(translator).orchestrator
    run = orch.run(_build_turn(GUJARATI), _ctx())
    async for event in run:
        if isinstance(event, ClaimDelta):
            break
    # Still open: a stream that had finished would read closed with or
    # without `aclosing`.
    assert not translator.stream_closed

    await run.aclose()

    assert translator.stream_closed


# --- no route --------------------------------------------------------------------


async def test_unlisted_language_calls_no_translator() -> None:
    translator = FakeTranslator()
    orch = _build_translating(translator).orchestrator

    await _drain(orch, _build_turn(MARATHI))

    assert translator.calls == []


async def test_untranslated_answer_still_streams_piece_by_piece() -> None:
    pieces = ("It will ", "rain ", "today [1].")
    orch = _build_translating(
        FakeTranslator(), compose=_FakeCompose("", chunks=pieces)
    ).orchestrator

    events = await _drain(orch, _build_turn(ENGLISH))

    assert _collect_streamed_texts(events) == list(pieces)


# --- traces ----------------------------------------------------------------------


@pytest.fixture
def spans(monkeypatch) -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr("opentelemetry.trace.get_tracer_provider", lambda: provider)
    set_stage_span_opener(open_span)
    yield exporter
    set_stage_span_opener(None)


def _read_span_contents(
    spans: InMemorySpanExporter, name: str
) -> list[tuple[str, str]]:
    return [
        (
            span.attributes["langfuse.observation.input"],
            span.attributes["langfuse.observation.output"],
        )
        for span in spans.get_finished_spans()
        if span.name == name
    ]


async def test_translation_is_traced_both_ways_and_the_turn_keeps_the_farmers_words(
    spans, monkeypatch
) -> None:
    monkeypatch.setenv("DSS_TRACE_INCLUDE_MESSAGE_CONTENT", "true")
    orch = _build_translating(FakeTranslator()).orchestrator

    await _drain(orch, _build_turn(GUJARATI))

    assert _read_span_contents(spans, "dss.stage.translation_in") == [
        (QUERY, f"[en] {QUERY}")
    ]
    assert _read_span_contents(spans, "dss.stage.translation_out") == [
        (ANSWER, f"[gu] {ANSWER}")
    ]
    assert _read_span_contents(spans, "dss.turn") == [(QUERY, f"[gu] {ANSWER}")]
