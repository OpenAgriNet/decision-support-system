"""Tier 1 — citations moved to the end of each paragraph of a streamed answer."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable

from dss.core.stream_response.citations import CitationNumbers, cite_per_paragraph


async def _pieces(*pieces: str) -> AsyncIterator[str]:
    for piece in pieces:
        yield piece


async def _joined(pieces: Iterable[str]) -> str:
    return "".join([piece async for piece in cite_per_paragraph(_pieces(*pieces))])


async def test_a_source_cited_in_every_sentence_is_cited_once_at_the_end() -> None:
    text = await _joined(["Monday is dry [1]. ", "Tuesday is wet [1]."])

    assert text == "Monday is dry. Tuesday is wet. [1]"


async def test_a_marker_split_across_pieces_is_still_removed() -> None:
    """The model breaks text anywhere, mid-marker included."""

    text = await _joined(["Monday is dry [", "1]. Tuesday is wet", " [", "1", "]."])

    assert text == "Monday is dry. Tuesday is wet. [1]"


async def test_two_sources_are_each_cited_once_in_first_cited_order() -> None:
    text = await _joined(["Rain today [2]. ", "Onion at 2200 [1]. Rain again [2]."])

    assert text == "Rain today. Onion at 2200. Rain again. [2][1]"


async def test_each_paragraph_cites_its_own_sources() -> None:
    text = await _joined(["Plant in June [1]. Water [1].\n\nPune is dry [2]."])

    assert text == "Plant in June. Water. [1]\n\nPune is dry. [2]"


async def test_a_paragraph_break_split_across_pieces_still_ends_a_paragraph() -> None:
    text = await _joined(["Plant in June [1].\n", "\nPune is dry [2]."])

    assert text == "Plant in June. [1]\n\nPune is dry. [2]"


async def test_a_source_used_in_two_paragraphs_is_cited_in_both() -> None:
    text = await _joined(["Mango [1].\n\nAlso mango [1]."])

    assert text == "Mango. [1]\n\nAlso mango. [1]"


async def test_a_paragraph_citing_nothing_gets_no_marker() -> None:
    text = await _joined(["Hello.\n\nPune is dry [2]."])

    assert text == "Hello.\n\nPune is dry. [2]"


async def test_trailing_space_does_not_double_before_the_marker() -> None:
    text = await _joined(["Dry [1]. \n\nWet [2]. "])

    assert text == "Dry. [1]\n\nWet. [2]"


async def test_text_without_markers_comes_through_unchanged() -> None:
    """Held-back characters that turn out not to be a marker are still sent."""

    text = await _joined(["Prices [", "per quintal] vary. ", "Ask again ["])

    assert text == "Prices [per quintal] vary. Ask again ["


def _renumbered(known: tuple[str, ...], *pieces: str) -> str:
    numbers = CitationNumbers(known)
    return "".join([numbers.feed(piece) for piece in pieces]) + numbers.flush()


def test_the_only_cited_source_is_numbered_one() -> None:
    assert _renumbered(("1", "2"), "Plant in June. [2]") == "Plant in June. [1]"


def test_sources_are_numbered_in_the_order_first_cited() -> None:
    text = _renumbered(("1", "2", "3"), "Rain [3]. Mango [1]. Rain [3].")

    assert text == "Rain [1]. Mango [2]. Rain [1]."


def test_a_marker_split_across_pieces_is_renumbered() -> None:
    assert _renumbered(("1", "2"), "Dry [", "2", "]. Wet [2", "]") == "Dry [1]. Wet [1]"


def test_a_marker_naming_no_source_is_removed() -> None:
    assert _renumbered(("1",), "Onion 2200. Rain. [7][1]") == "Onion 2200. Rain. [1]"


def test_the_numbers_map_back_to_the_composer_sources() -> None:
    numbers = CitationNumbers(("1", "2", "3"))
    numbers.feed("Rain [3]. Mango [1].")

    assert numbers.cited == {"3": "1", "1": "2"}


def test_the_composed_text_keeps_the_composer_numbers() -> None:
    numbers = CitationNumbers(("1", "2"))
    numbers.feed("Rain [2].")

    assert numbers.composed == "Rain [2]."
