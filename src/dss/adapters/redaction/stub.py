"""The redactor for when redaction is off: every text comes back as it went in.

It lets the orchestrator always call a ``Redactor``, rather than checking
whether there is one. The map it returns is empty, so the provider call swaps
nothing and hides nothing.
"""

from __future__ import annotations

from collections.abc import Sequence

from dss.core.redaction.models import Normalise
from dss.core.redaction.reveal import RevealMap
from dss.core.redaction.service import Redaction


class StubRedactor:
    async def redact(self, texts: Sequence[str]) -> Redaction:
        return Redaction(
            texts=tuple(texts),
            reveal=RevealMap(values={}, normalise=Normalise()),
            found={},
        )
