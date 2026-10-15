"""Citations moved to the end of each paragraph of a streamed answer.

- A small model cites every sentence, and no prompt stopped it.
- One marker per source per paragraph keeps the citation near its text.
"""

from __future__ import annotations

import re
from collections.abc import AsyncGenerator, AsyncIterator, Iterable

# The space before a marker goes with it, so "dry [1]." becomes "dry.".
_MARKER = re.compile(r" ?\[(\d+)\]")
# Held back until the next piece says what it is: part of a marker, or the
# first half of a paragraph break.
_MAYBE_MORE = re.compile(r"(?: ?\[\d*| |\n)$")
_PARAGRAPH = "\n\n"


def _cite(text: str, cited: list[str]) -> str:
    if not cited:
        return text
    return text.rstrip() + " " + "".join(f"[{source_id}]" for source_id in cited)


async def cite_per_paragraph(pieces: AsyncIterator[str]) -> AsyncGenerator[str]:
    """Pass the text on without its `[n]` markers, and end each paragraph with
    each number it cited, once, in the order first cited."""

    cited: list[str] = []
    held = ""
    async for piece in pieces:
        text = held + piece
        maybe = _MAYBE_MORE.search(text)
        cut = maybe.start() if maybe else len(text)
        text, held = text[:cut], text[cut:]
        *ended, current = text.split(_PARAGRAPH)
        out = ""
        for paragraph in ended:
            cited.extend(n for n in _MARKER.findall(paragraph) if n not in cited)
            out += _cite(_MARKER.sub("", paragraph), cited) + _PARAGRAPH
            cited = []
        cited.extend(n for n in _MARKER.findall(current) if n not in cited)
        out += _MARKER.sub("", current)
        if out:
            yield out
    if tail := _cite(held, cited):
        yield tail


class CitationNumbers:
    """Renumbers cited sources 1..k in first-cited order; unknown ones are
    dropped. `cited` maps the composer's number to the farmer's."""

    def __init__(self, known: Iterable[str]) -> None:
        self._known = set(known)
        self.cited: dict[str, str] = {}
        self._held = ""

    def feed(self, piece: str) -> str:
        text = self._held + piece
        maybe = _PART_MARKER.search(text)
        cut = maybe.start() if maybe else len(text)
        text, self._held = text[:cut], text[cut:]
        return _BARE_MARKER.sub(self._renumber, text)

    def flush(self) -> str:
        held, self._held = self._held, ""
        return _BARE_MARKER.sub(self._renumber, held)

    def _renumber(self, marker: re.Match[str]) -> str:
        source_id = marker.group(1)
        if source_id not in self._known:
            return ""
        number = self.cited.setdefault(source_id, str(len(self.cited) + 1))
        return f"[{number}]"


_BARE_MARKER = re.compile(r"\[(\d+)\]")
_PART_MARKER = re.compile(r"\[\d*$")
