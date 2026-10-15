"""Every `TurnRunner` and `Translator` implementation, exercised through the
port itself.

`isinstance` against a Protocol checks method *names*, not signatures, and this
repo has no type checker — so nothing else would catch an implementation whose
`run` takes `(turn, context)` while the port says `(turn, ctx)`. Calling through
a port-annotated parameter does.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from dss.core.shared.language import ENGLISH, LanguageTag
from dss.core.shared.models import TurnContext, TurnEvent, TurnFinished, UserTurn
from dss.orchestration.stub_runner import StubRunner
from dss.ports.translator import Translator
from dss.ports.turn import TurnRunner
from tests.support.fakes import (
    FakeAreaLookup,
    FakeLocalizer,
    FakePromptProvider,
    FakeRunner,
    FakeSchemeCatalog,
    FakeTranslator,
)


def drive(runner: TurnRunner, turn: UserTurn, ctx: TurnContext) -> list[TurnEvent]:
    """Deliberately typed as the port, not as the implementation."""

    async def _collect() -> list[TurnEvent]:
        return [event async for event in runner.run(turn, ctx)]

    return asyncio.run(_collect())


def test_the_stub_runner_satisfies_the_port(a_turn, a_context):
    events = drive(StubRunner(), a_turn(), a_context)

    assert isinstance(events[-1], TurnFinished)


def test_the_fake_runner_satisfies_the_port(a_turn, a_context):
    events = drive(FakeRunner([StubRunner.ANSWER]), a_turn(), a_context)

    assert events == [StubRunner.ANSWER]


async def _no_discovery(intent, turn, *, now):
    """Conformance is about the port's shape, not discovery."""

    from dss.core.provider_discovery.models import DiscoveryResult

    return DiscoveryResult(answers={}, capabilities={}, failures={}, events=())


async def _unreached_plan(turn, *, intent, discovery, verdict, visibility):
    """With `_no_discovery` nobody serves the ask, so the orchestrator answers
    NO_MATCH before the planner runs — this must never be called."""

    raise AssertionError("the planner ran despite an empty discovery")


async def _unreached_compose(evidence, *, turn):
    raise AssertionError("the composer ran despite an empty discovery")


def test_the_orchestrator_satisfies_the_port(a_turn, a_context):
    from dss.adapters.llm.stub import StubLLM
    from dss.adapters.sinks.memory import MemoryTurnSink
    from dss.adapters.sinks.stdout import StdoutTelemetrySink
    from dss.config.clarification_text_loader import load_clarification_text
    from dss.core.translation.service import EdgeTranslation
    from dss.orchestration.orchestrator import Components, Orchestrator
    from dss.orchestration.redaction import pass_through

    runner = Orchestrator(
        intent_llm=StubLLM(),
        moderation_llm=StubLLM(),
        prompts=FakePromptProvider(),
        policies=[],
        scheme_catalog=FakeSchemeCatalog(),
        scheme_fuzzy_threshold=None,
        nearest_max_km=50.0,
        components=Components(
            redact=pass_through,
            discover=_no_discovery,
            plan=_unreached_plan,
            compose=_unreached_compose,
            localize=FakeLocalizer(),
        ),
        turns=MemoryTurnSink(),
        telemetry=StdoutTelemetrySink(),
        # Empty: this test is about the port contract, not about where the turn
        # searches. Whichever way the turn ends, it ends in a TurnFinished.
        area_lookup=FakeAreaLookup(),
        schemas={},
        clarification_text=load_clarification_text(),
        edge_translation=EdgeTranslation(routes={}),
    )

    events = drive(runner, a_turn(), a_context)

    assert isinstance(events[-1], TurnFinished)


def translate_through(
    translator: Translator,
    texts: Sequence[str],
    *,
    from_lang: LanguageTag,
    to_lang: LanguageTag,
) -> tuple[str, ...]:
    """Deliberately typed as the port, not as the implementation."""

    return asyncio.run(
        translator.translate(texts, from_lang=from_lang, to_lang=to_lang)
    )


def stream_through(
    translator: Translator, text: str, *, from_lang: LanguageTag, to_lang: LanguageTag
) -> list[str]:
    """Deliberately typed as the port, not as the implementation."""

    async def _collect() -> list[str]:
        return [
            piece
            async for piece in translator.stream(
                text, from_lang=from_lang, to_lang=to_lang
            )
        ]

    return asyncio.run(_collect())


def test_the_fake_translator_satisfies_the_port():
    translator = FakeTranslator()
    gujarati = LanguageTag("gu")

    translated = translate_through(
        translator, ["a", "b"], from_lang=gujarati, to_lang=ENGLISH
    )
    pieces = stream_through(translator, "c", from_lang=ENGLISH, to_lang=gujarati)

    assert isinstance(translator, Translator)
    assert translated == ("[en] a", "[en] b")
    assert "".join(pieces) == "[gu] c"


def test_the_stub_translator_satisfies_the_port():
    from dss.adapters.translator.stub import CANNED_QUESTION, StubTranslator

    translator = StubTranslator(language="gu")
    gujarati = LanguageTag("gu")

    translated = translate_through(
        translator, ["a", "b"], from_lang=gujarati, to_lang=ENGLISH
    )
    pieces = stream_through(translator, "c", from_lang=ENGLISH, to_lang=gujarati)

    assert isinstance(translator, Translator)
    assert translated == (CANNED_QUESTION, CANNED_QUESTION)
    assert "".join(pieces) == "[gu] c"


def test_the_llm_translator_satisfies_the_port():
    from dss.adapters.llm.stub import StubLLM
    from dss.adapters.translator.llm import LLMTranslator, TranslatedTexts

    translator = LLMTranslator(
        llm=StubLLM(
            {TranslatedTexts: TranslatedTexts(texts=["a", "b"])},
            text_chunks=("[gu] ", "c"),
        ),
        prompts=FakePromptProvider(),
    )
    gujarati = LanguageTag("gu")

    translated = translate_through(
        translator, ["અ", "બ"], from_lang=gujarati, to_lang=ENGLISH
    )
    pieces = stream_through(translator, "c", from_lang=ENGLISH, to_lang=gujarati)

    assert isinstance(translator, Translator)
    assert translated == ("a", "b")
    assert "".join(pieces) == "[gu] c"
