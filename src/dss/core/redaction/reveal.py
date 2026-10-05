"""The one object that holds a turn's real values.

Built each turn and thrown away with it. It goes to two places. The call to a
provider uses it twice:

- ``reveal`` swaps each tag it holds for the real value in the request body. A
  tag it does not hold (a destroyed Aadhaar) goes out as the tag.
- ``conceal`` swaps a real value back to its tag when the provider echoes it
  ("status for 9876543210"), so it does not re-enter prompts, traces or logs.
  Numbers the farmer did not type, like a KVK officer's, are left alone.

The farmer's own answer uses it once, through ``StreamReveal``: the tags the
composer wrote are swapped back as the answer goes out, so the farmer reads
their own number, not «phone_1». The audit record keeps the tags.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from dss.core.redaction.normalise import join_number_gaps, national_part

TAG = re.compile(r"«[a-z][a-z0-9_]*_\d+»")
# The start of a tag that has not closed yet — «, then only tag characters.
_OPEN_TAG = re.compile(r"«[a-z0-9_]*\Z")


@dataclass(frozen=True, slots=True)
class RevealMap:
    values: Mapping[str, str]  # tag → real value

    def reveal(self, body: Any) -> Any:
        """A copy of ``body`` with every held tag swapped for its value."""

        if isinstance(body, str):
            return TAG.sub(lambda m: self.values.get(m.group(), m.group()), body)
        if isinstance(body, Mapping):
            return {key: self.reveal(item) for key, item in body.items()}
        if isinstance(body, list | tuple):
            return type(body)(self.reveal(item) for item in body)
        return body

    def conceal(self, text: str) -> str:
        """``text`` with every held value swapped back to its tag, however it is
        spaced (``98765 43210`` hides as ``9876543210`` does)."""

        if not self.values:
            return text

        joined = join_number_gaps(text)
        spans: list[tuple[int, int, str]] = []
        for tag, value in self.values.items():
            for match in _echo(value).finditer(joined.text):
                start, end = joined.map_to_original(match.start(), match.end())
                spans.append((start, end, tag))

        out: list[str] = []
        last = 0
        for start, end, tag in sorted(spans, key=lambda s: (s[0], s[0] - s[1])):
            if start < last:
                continue
            out.append(text[last:start])
            out.append(tag)
            last = end
        out.append(text[last:])
        return "".join(out)


def _echo(value: str) -> re.Pattern[str]:
    """How a held value can come back. A number the farmer wrote with a country
    code can come back with it, with another code, or as the bare ten digits."""

    national = national_part(value)
    if national is not None:
        return re.compile(rf"(?<![\w+])(?:(?:\+|00)\d{{1,3}})?{national}(?!\w)")
    return re.compile(rf"(?<!\w){re.escape(value)}(?!\w)", re.IGNORECASE)


class StreamReveal:
    """Reveals an answer that arrives in pieces.

    A tag can be split across two pieces («pho + ne_1»). The unfinished end of
    a piece is held back until the next one shows whether it is a tag, so the
    farmer never sees half a tag. ``flush`` returns whatever is still held."""

    def __init__(self, reveal: RevealMap) -> None:
        self._reveal = reveal
        self._held = ""

    def feed(self, piece: str) -> str:
        text = self._held + piece
        self._held = ""
        if self._reveal.values:
            opening = _OPEN_TAG.search(text)
            if opening is not None:
                self._held = text[opening.start() :]
                text = text[: opening.start()]
        return self._reveal.reveal(text)

    def flush(self) -> str:
        held, self._held = self._held, ""
        return held
