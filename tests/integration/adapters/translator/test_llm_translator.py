"""Tier 2 — the LLM translator, against a recording `LLMProvider`.

`StubLLM` stands in for the model: it records what it was asked and answers
from a table, so these tests pin what the adapter sends, not what a model
writes.
"""

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
    translator = LLMTranslator(llm=llm, prompts=FakePromptProvider())

    translated = await translator.translate(
        ["આજે વરસાદ?", "નમસ્તે"], from_lang=GUJARATI, to_lang=ENGLISH
    )

    assert translated == ("Rain today?", "Hello")
    (call,) = llm.calls
    assert call.schema is TranslatedTexts
    assert json.loads(call.user_query) == ["આજે વરસાદ?", "નમસ્તે"]


async def test_stream_relays_pieces_that_join_to_the_whole():
    llm = StubLLM(text_chunks=("આજે ", "વરસાદ ", "[1]"))
    translator = LLMTranslator(llm=llm, prompts=FakePromptProvider())

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
    translator = LLMTranslator(llm=llm, prompts=prompts)

    await translator.translate(["આજે વરસાદ?"], from_lang=GUJARATI, to_lang=ENGLISH)
    _ = [p async for p in translator.stream("x", from_lang=ENGLISH, to_lang=GUJARATI)]

    assert prompts.calls == [
        (
            PromptIdentifier.TRANSLATOR,
            ENGLISH,
            {"translate_from": "gu", "translate_to": "en"},
        ),
        (
            PromptIdentifier.TRANSLATOR,
            ENGLISH,
            {"translate_from": "en", "translate_to": "gu"},
        ),
    ]
    assert all(
        "[translator prompt, lang=en]" in call.system_prompt for call in llm.calls
    )
