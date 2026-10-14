"""Tier 2 — the stub translator, which lets the frame run with no model.

Any text in the stub's own language becomes one canned question; anything else
comes back marked with the language it was sent to.
"""

from __future__ import annotations

from dss.adapters.translator.stub import CANNED_QUESTION, StubTranslator
from dss.core.shared.language import ENGLISH, LanguageTag

GUJARATI = LanguageTag("gu")


async def test_stub_turns_any_farmer_text_into_the_canned_question():
    stub = StubTranslator(language="gu")

    translated = await stub.translate(
        ["આજે વરસાદ પડશે?", "મારા ઘઉં"], from_lang=GUJARATI, to_lang=ENGLISH
    )

    assert translated == (CANNED_QUESTION, CANNED_QUESTION)


async def test_stub_marks_what_it_sends_back():
    stub = StubTranslator(language="gu")

    translated = await stub.translate(
        ["Light rain is expected.", "Call «phone_1»."],
        from_lang=ENGLISH,
        to_lang=GUJARATI,
    )

    assert all(text.startswith("[gu] ") for text in translated)


async def test_stub_streams_the_marked_answer_in_pieces():
    stub = StubTranslator(language="gu")
    answer = "Light rain is expected [1]."

    pieces = [
        piece
        async for piece in stub.stream(answer, from_lang=ENGLISH, to_lang=GUJARATI)
    ]
    (whole,) = await stub.translate([answer], from_lang=ENGLISH, to_lang=GUJARATI)

    assert len(pieces) >= 2
    assert "".join(pieces) == whole


async def test_stub_records_what_it_was_asked():
    stub = StubTranslator(language="gu")

    await stub.translate(["આજે"], from_lang=GUJARATI, to_lang=ENGLISH)
    _ = [
        piece
        async for piece in stub.stream("Rain.", from_lang=ENGLISH, to_lang=GUJARATI)
    ]

    assert [(call.texts, call.from_lang, call.to_lang) for call in stub.calls] == [
        (("આજે",), GUJARATI, ENGLISH),
        (("Rain.",), ENGLISH, GUJARATI),
    ]
