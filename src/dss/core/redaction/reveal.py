"""The one object that holds a turn's real values.

Built each turn and thrown away with it. It goes to exactly one place — the call
to a provider — which uses it twice:

- ``reveal`` swaps each tag it holds for the real value in the request body. A
  tag it does not hold (a destroyed Aadhaar) goes out as the tag.
- ``conceal`` swaps a real value back to its tag when the provider echoes it
  ("status for 9876543210"), so it does not re-enter prompts, traces or logs.
  Numbers the farmer did not type, like a KVK officer's, are left alone.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from dss.core.redaction.normalise import join_number_gaps

TAG = re.compile(r"«[a-z][a-z0-9_]*_\d+»")


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
            echo = re.compile(rf"(?<!\w){re.escape(value)}(?!\w)", re.IGNORECASE)
            for match in echo.finditer(joined.text):
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
