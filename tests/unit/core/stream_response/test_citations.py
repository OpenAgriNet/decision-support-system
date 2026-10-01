"""Tier 1 — citations moved to the end of a streamed answer.

A prompt cannot make every model cite a source once: a small one cites every
sentence. So the code removes the markers as the text streams and sends each
cited number once, after the last piece.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable

from dss.core.stream_response.citations import cite_at_end


async def _pieces(*pieces: str) -> AsyncIterator[str]:
    for piece in pieces:
        yield piece


async def _joined(pieces: Iterable[str]) -> str:
    return "".join([piece async for piece in cite_at_end(_pieces(*pieces))])


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


async def test_text_without_markers_comes_through_unchanged() -> None:
    """Held-back characters that turn out not to be a marker are still sent."""

    text = await _joined(["Prices [", "per quintal] vary. ", "Ask again ["])

    assert text == "Prices [per quintal] vary. Ask again ["
