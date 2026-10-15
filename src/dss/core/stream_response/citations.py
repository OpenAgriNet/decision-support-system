"""Citations moved to the end of a streamed answer.

The prompt asks the model to cite its sources, but not every model can be
told where: a small one cites every sentence. A citation already sent cannot
be taken back, so the rule is kept here, in code, for any model.
"""

from __future__ import annotations

import re
from collections.abc import AsyncGenerator, AsyncIterator, Iterable

# The space before a marker goes with it, so "dry [1]." becomes "dry.".
_MARKER = re.compile(r" ?\[(\d+)\]")
# The end of a piece that may be the start of a marker: " ", "[", " [1".
# Held back until the next piece says what it is — a few characters, never
# the answer.
_MAYBE_MARKER = re.compile(r" ?(\[\d*)?$")


async def cite_at_end(pieces: AsyncIterator[str]) -> AsyncGenerator[str]:
    """Pass the text on without its `[n]` markers, then send each cited
    number once, in the order first cited."""

    cited: list[str] = []
    held = ""
    async for piece in pieces:
        text = held + piece
        for source_id in _MARKER.findall(text):
            if source_id not in cited:
                cited.append(source_id)
        text = _MARKER.sub("", text)
        maybe = _MAYBE_MARKER.search(text)
        cut = maybe.start() if maybe else len(text)
        text, held = text[:cut], text[cut:]
        if text:
            yield text
    if held:
        yield held
    if cited:
        yield " " + "".join(f"[{source_id}]" for source_id in cited)


class CitationNumbers:
    """Renumbers `[n]` markers so the sources the answer used read [1], [2], …

    The composer sees every source read for the turn, numbered as they were
    gathered. It uses some of them, so its numbers can start at [2] or skip
    one. The farmer is shown only the sources used, so they are numbered
    again, in the order first cited. A number naming no source is removed.

    Fed piece by piece like `StreamReveal`: a marker split across two pieces
    is held back until it is whole. `cited` maps the composer's number to the
    farmer's.
    """

    def __init__(self, known: Iterable[str]) -> None:
        self._known = set(known)
        self.cited: dict[str, str] = {}
        self._held = ""
        self._composed: list[str] = []

    @property
    def composed(self) -> str:
        """Everything fed, with the composer's own numbers."""

        return "".join(self._composed)

    async def renumber(self, pieces: AsyncIterator[str]) -> AsyncGenerator[str]:
        async for piece in pieces:
            if renumbered := self.feed(piece):
                yield renumbered
        if rest := self.flush():
            yield rest

    def feed(self, piece: str) -> str:
        self._composed.append(piece)
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


# Spaces are left alone: holding one back would re-split pieces the model
# sent, and `cite_at_end` has already tidied them.
_BARE_MARKER = re.compile(r"\[(\d+)\]")
_PART_MARKER = re.compile(r"\[\d*$")
