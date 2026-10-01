"""Citations moved to the end of a streamed answer.

The prompt asks the model to cite its sources, but not every model can be
told where: a small one cites every sentence. A citation already sent cannot
be taken back, so the rule is kept here, in code, for any model.
"""

from __future__ import annotations

import re
from collections.abc import AsyncGenerator, AsyncIterator

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
