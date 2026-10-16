"""Tier 2 — the LLM translator, with `StubLLM` recording what it is asked."""

from __future__ import annotations

import json

from dss.adapters.llm.stub import StubLLM
from dss.adapters.translator.llm import LLMTranslator, TranslatedTexts
from dss.core.shared.language import ENGLISH, LanguageTag
from dss.ports.prompts import PromptIdentifier
from tests.support.fakes import FakePromptProvider

GUJARATI = LanguageTag("gu")


async def test_translate_is_one_structured_call_in_order():
    llm = StubLLM({TranslatedTexts: TranslatedTexts(texts=["Rain today?", "Hello"])})
    translator = LLMTranslator(
        batch_llm=llm, stream_llm=llm, prompts=FakePromptProvider()
    )

    translated = await translator.translate(
        ["આજે વરસાદ?", "નમસ્તે"], from_lang=GUJARATI, to_lang=ENGLISH
    )

    assert translated == ("Rain today?", "Hello")
    (call,) = llm.calls
    assert call.schema is TranslatedTexts
    assert json.loads(call.user_query) == ["આજે વરસાદ?", "નમસ્તે"]


async def test_stream_relays_pieces_that_join_to_the_whole():
    llm = StubLLM(text_chunks=("આજે ", "વરસાદ ", "[1]"))
    translator = LLMTranslator(
        batch_llm=llm, stream_llm=llm, prompts=FakePromptProvider()
    )

    pieces = [
        piece
        async for piece in translator.stream(
            "Rain today [1]", from_lang=ENGLISH, to_lang=GUJARATI
        )
    ]

    assert pieces == ["આજે ", "વરસાદ ", "[1]"]
    (call,) = llm.calls
    assert call.schema is None
    assert call.user_query == "Rain today [1]"


async def test_prompt_is_asked_in_english_with_the_pair_as_placeholders():
    prompts = FakePromptProvider()
    llm = StubLLM(
        {TranslatedTexts: TranslatedTexts(texts=["Rain today?"])}, text_chunks=("x",)
    )
    translator = LLMTranslator(batch_llm=llm, stream_llm=llm, prompts=prompts)

    await translator.translate(["આજે વરસાદ?"], from_lang=GUJARATI, to_lang=ENGLISH)
    _ = [p async for p in translator.stream("x", from_lang=ENGLISH, to_lang=GUJARATI)]

    assert prompts.calls == [
        (
            PromptIdentifier.TRANSLATOR,
            ENGLISH,
            {"translate_from": GUJARATI, "translate_to": ENGLISH},
        ),
        (
            PromptIdentifier.TRANSLATOR,
            ENGLISH,
            {"translate_from": ENGLISH, "translate_to": GUJARATI},
        ),
    ]
    assert all(
        "[translator prompt, lang=en]" in call.system_prompt for call in llm.calls
    )


async def test_batches_and_streams_go_to_their_own_binding():
    """One binding per direction, so each is timed and costed on its own."""

    batch_llm = StubLLM({TranslatedTexts: TranslatedTexts(texts=["Rain?"])})
    stream_llm = StubLLM(text_chunks=("વરસાદ",))
    translator = LLMTranslator(
        batch_llm=batch_llm, stream_llm=stream_llm, prompts=FakePromptProvider()
    )

    await translator.translate(["વરસાદ?"], from_lang=GUJARATI, to_lang=ENGLISH)
    _ = [
        p async for p in translator.stream("Rain", from_lang=ENGLISH, to_lang=GUJARATI)
    ]

    assert [call.schema for call in batch_llm.calls] == [TranslatedTexts]
    assert [call.schema for call in stream_llm.calls] == [None]
