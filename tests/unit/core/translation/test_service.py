"""Tier 1 — translating a turn in and an answer back out, against a fake."""

from __future__ import annotations

import pytest

from dss.core.channel.models import ComposedAnswer
from dss.core.shared.errors import TranslationError
from dss.core.shared.language import ENGLISH, LanguageTag
from dss.core.shared.models import (
    ConversationMessage,
    RefusalBlock,
    Source,
    SourceKind,
    TextBlock,
    UserDetails,
)
from dss.core.translation.models import TranslationRoute
from dss.core.translation.service import EdgeTranslation, with_translated_blocks
from tests.support.fakes import FakeTranslator

GUJARATI = LanguageTag("gu")


@pytest.fixture
def a_gujarati_turn(a_turn):
    def _turn(**overrides):
        return a_turn(
            **{
                "query": "આજે વરસાદ પડશે?",
                "source_lang": GUJARATI,
                "target_lang": GUJARATI,
                "history": [
                    ConversationMessage(role="user", text="નમસ્તે"),
                    ConversationMessage(role="assistant", text="Hello"),
                ],
            }
            | overrides
        )

    return _turn


def _edge_translation(
    translator: FakeTranslator, to: LanguageTag = ENGLISH
) -> EdgeTranslation:
    return EdgeTranslation(
        routes={"gu": TranslationRoute(translator=translator, to=to)}
    )


def _translation(turn, translator: FakeTranslator, to: LanguageTag = ENGLISH):
    translation = _edge_translation(translator, to).for_turn(turn)
    assert translation is not None
    return translation


def _an_answer() -> ComposedAnswer:
    return ComposedAnswer(
        content=(
            TextBlock(text="Rain is likely today [1]", source_ids=("1",)),
            RefusalBlock(text="I can't help with gold prices"),
        ),
        sources=(Source(id="1", name="IMD", kind=SourceKind.PROVIDER),),
    )


def test_region_tag_matches_its_language(a_gujarati_turn):
    translator = FakeTranslator()

    translation = _edge_translation(translator).for_turn(
        a_gujarati_turn(target_lang=LanguageTag("gu-IN"))
    )

    assert translation is not None
    assert translation.translator is translator


def test_unlisted_language_has_no_translation(a_gujarati_turn):
    turn = a_gujarati_turn(target_lang=LanguageTag("mr"))

    assert _edge_translation(FakeTranslator()).for_turn(turn) is None


def test_no_routes_means_no_translation(a_gujarati_turn):
    assert EdgeTranslation(routes={}).for_turn(a_gujarati_turn()) is None


def test_translation_keeps_the_farmers_full_tag_and_the_routes_language(
    a_gujarati_turn,
):
    marathi = LanguageTag("mr")
    turn = a_gujarati_turn(target_lang=LanguageTag("gu-IN"))

    translation = _translation(turn, FakeTranslator(), to=marathi)

    assert translation.edge_lang == LanguageTag("gu-IN")
    assert translation.core_lang == marathi


async def test_translate_in_is_one_call_from_the_edge_to_the_core(a_gujarati_turn):
    translator = FakeTranslator()
    turn = a_gujarati_turn()

    await _translation(turn, translator).translate_in(turn)

    [(method, _, from_lang, to_lang)] = translator.calls
    assert method == "translate"
    assert (from_lang, to_lang) == (GUJARATI, ENGLISH)


async def test_translate_in_skips_an_unchanged_enriched_query(a_gujarati_turn):
    translator = FakeTranslator()
    turn = a_gujarati_turn()

    result = await _translation(turn, translator).translate_in(turn)

    [(_, texts, _, _)] = translator.calls
    assert texts == ("આજે વરસાદ પડશે?", "નમસ્તે", "Hello")
    assert result.enriched_query == result.original_query


async def test_translate_in_sends_an_enriched_query_that_differs(a_gujarati_turn):
    translator = FakeTranslator()
    turn = a_gujarati_turn(enriched_query="આજે આણંદમાં વરસાદ પડશે?")

    result = await _translation(turn, translator).translate_in(turn)

    [(_, texts, _, _)] = translator.calls
    assert texts[:2] == ("આજે વરસાદ પડશે?", "આજે આણંદમાં વરસાદ પડશે?")
    assert result.enriched_query == "[en] આજે આણંદમાં વરસાદ પડશે?"
    assert [message.text for message in result.history] == ["[en] નમસ્તે", "[en] Hello"]


async def test_translate_in_never_sends_the_phone(a_gujarati_turn):
    translator = FakeTranslator()
    turn = a_gujarati_turn(user=UserDetails(phone="«phone_1»"))

    result = await _translation(turn, translator).translate_in(turn)

    [(_, texts, _, _)] = translator.calls
    assert "«phone_1»" not in texts
    assert result.user.phone == "«phone_1»"


async def test_translate_in_sets_both_languages_to_the_core_language(
    a_gujarati_turn,
):
    marathi = LanguageTag("mr")
    turn = a_gujarati_turn()

    result = await _translation(turn, FakeTranslator(), to=marathi).translate_in(turn)

    assert isinstance(result.target_lang, LanguageTag)
    assert result.source_lang == result.target_lang == marathi


async def test_translate_in_leaves_the_farmers_turn_as_it_was(a_gujarati_turn):
    turn = a_gujarati_turn()

    await _translation(turn, FakeTranslator()).translate_in(turn)

    assert turn == a_gujarati_turn()


async def test_empty_history_text_keeps_its_slot(a_gujarati_turn):
    translator = FakeTranslator()
    turn = a_gujarati_turn(
        history=[
            ConversationMessage(role="user", text="નમસ્તે"),
            ConversationMessage(role="assistant", text=""),
            ConversationMessage(role="user", text="આભાર"),
        ]
    )

    result = await _translation(turn, translator).translate_in(turn)

    [(_, texts, _, _)] = translator.calls
    assert "" not in texts
    assert [message.text for message in result.history] == [
        "[en] નમસ્તે",
        "",
        "[en] આભાર",
    ]


async def test_translate_in_rejects_a_wrong_count(a_gujarati_turn):
    turn = a_gujarati_turn()
    translation = _translation(turn, FakeTranslator(drop_one=True))

    with pytest.raises(TranslationError):
        await translation.translate_in(turn)


async def test_translate_out_goes_from_the_core_to_the_edge(a_gujarati_turn):
    translator = FakeTranslator()

    result = await _translation(a_gujarati_turn(), translator).translate_out(
        _an_answer()
    )

    [(method, texts, from_lang, to_lang)] = translator.calls
    assert method == "translate"
    assert texts == ("Rain is likely today [1]", "I can't help with gold prices")
    assert (from_lang, to_lang) == (ENGLISH, GUJARATI)
    assert [block.text for block in result.content] == [
        "[gu] Rain is likely today [1]",
        "[gu] I can't help with gold prices",
    ]


async def test_stream_out_goes_from_the_core_to_the_edge(a_gujarati_turn):
    translator = FakeTranslator()
    translation = _translation(a_gujarati_turn(), translator)

    pieces = [piece async for piece in translation.stream_out("Rain is likely")]

    [(method, texts, from_lang, to_lang)] = translator.calls
    assert (method, texts) == ("stream", ("Rain is likely",))
    assert (from_lang, to_lang) == (ENGLISH, GUJARATI)
    assert "".join(pieces) == "[gu] Rain is likely"


def test_translated_blocks_keep_type_and_sources():
    answer = _an_answer()

    result = with_translated_blocks(answer, ["વરસાદ [1]", "સોનું નહીં"])

    text_block, refusal_block = result.content
    assert isinstance(text_block, TextBlock)
    assert text_block.source_ids == ("1",)
    assert isinstance(refusal_block, RefusalBlock)
    assert result.sources == answer.sources


async def test_translate_out_rejects_a_wrong_count(a_gujarati_turn):
    translation = _translation(a_gujarati_turn(), FakeTranslator(drop_one=True))

    with pytest.raises(TranslationError):
        await translation.translate_out(_an_answer())
