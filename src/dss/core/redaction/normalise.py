"""A shadow copy of a text with number gaps joined — never the text that is kept.

People write ``1234 5678 9012`` and ``98765-43210``. Joining digit runs across a
short gap lets one comparison match every way of writing a number, and an offset
map takes a match back to the span in the original text.

Used by ``Visibility.conceal`` and by the regex identifier. Script folding
(``१२३४`` → ``1234``) belongs here too, when the translation story adds it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SEPARATORS = " -"
MAX_SEPARATORS = 2

# A number written with a country code ("+919876543210") is the same number as
# its last ten digits ("9876543210"). Ten is the length of the national part.
NATIONAL_DIGITS = 10
_INTERNATIONAL = re.compile(rf"(?:\+|00)\d*(\d{{{NATIONAL_DIGITS}}})")


def gap_pattern(
    separators: str = SEPARATORS, max_separators: int = MAX_SEPARATORS
) -> re.Pattern[str] | None:
    """The gap between two digits that joining removes, or ``None`` to join
    nothing. Built once, at startup, by whoever holds the settings."""

    if max_separators == 0 or not separators:
        return None
    return re.compile(rf"(?<=\d)[{re.escape(separators)}]{{1,{max_separators}}}(?=\d)")


# Core's own default, for ``conceal`` and tag matching. Built at import.
DEFAULT_GAP = gap_pattern()


@dataclass(frozen=True, slots=True)
class Shadow:
    text: str
    # offsets[i] is where shadow character i sits in the original text.
    offsets: tuple[int, ...]

    def map_to_original(self, start: int, end: int) -> tuple[int, int]:
        """The original span a non-empty shadow span ``[start, end)`` came from."""

        return self.offsets[start], self.offsets[end - 1] + 1


def join_number_gaps(text: str, *, gap: re.Pattern[str] | None = DEFAULT_GAP) -> Shadow:
    """``text`` with the gaps ``gap`` matches removed. ``None`` joins nothing."""

    if gap is None:
        return Shadow(text, tuple(range(len(text))))

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


def national_part(value: str) -> str | None:
    """The last ten digits of a number written with ``+`` or ``00`` in front,
    or ``None`` for anything else."""

    match = _INTERNATIONAL.fullmatch(join_number_gaps(value).text)
    return match.group(1) if match else None
