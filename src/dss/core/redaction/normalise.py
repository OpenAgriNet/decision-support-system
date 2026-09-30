"""The shadow copy patterns run over — never the text that is kept.

People write ``1234 5678 9012`` and ``98765-43210``. The shadow joins digit runs
across a short gap so one pattern catches every way of writing the number, and an
offset map takes a match back to the span in the original text.

Script folding (``१२३४`` → ``1234``) belongs here too, when the translation story
adds it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from dss.core.redaction.models import Normalise


@dataclass(frozen=True, slots=True)
class Shadow:
    text: str
    # offsets[i] is where shadow character i sits in the original text.
    offsets: tuple[int, ...]

    def to_original(self, start: int, end: int) -> tuple[int, int]:
        """The original span a non-empty shadow span ``[start, end)`` came from."""

        return self.offsets[start], self.offsets[end - 1] + 1


def shadow(text: str, normalise: Normalise) -> Shadow:
    if normalise.max_separators == 0 or not normalise.join_separators:
        return Shadow(text, tuple(range(len(text))))

    separators = re.escape("".join(normalise.join_separators))
    gap = re.compile(rf"(?<=\d)[{separators}]{{1,{normalise.max_separators}}}(?=\d)")

    pieces: list[str] = []
    offsets: list[int] = []
    last = 0
    for match in gap.finditer(text):
        pieces.append(text[last : match.start()])
        offsets.extend(range(last, match.start()))
        last = match.end()
    pieces.append(text[last:])
    offsets.extend(range(last, len(text)))
    return Shadow("".join(pieces), tuple(offsets))
