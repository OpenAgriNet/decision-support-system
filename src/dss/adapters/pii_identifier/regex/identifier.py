"""The regex identifier, behind the ``PiiIdentifier`` port.

Pure ``re`` and well under a millisecond a text, so it runs on the event loop
rather than a worker thread.
"""

from __future__ import annotations

from collections.abc import Sequence

from dss.adapters.pii_identifier.regex.identify import identify
from dss.adapters.pii_identifier.regex.models import RegexSettings
from dss.core.redaction.models import PiiSpan


class RegexIdentifier:
    name = "regex"

    def __init__(self, settings: RegexSettings) -> None:
        self._settings = settings

    async def identify(self, texts: Sequence[str]) -> list[list[PiiSpan]]:
        return [identify(text, self._settings) for text in texts]
