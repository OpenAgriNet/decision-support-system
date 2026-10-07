"""The seam for finding PII in text — implemented by adapters.

Regex rules, a name model, and an HTTP PII service are all identifiers. Config
lists which ones run; they run side by side, and core redacts from whatever
spans they report (``core/redaction/service.py``). A new identifier is a new
adapter and a config entry — nothing in core or the orchestrator changes.
"""

from __future__ import annotations

from typing import Protocol

from dss.core.redaction.models import PiiSpan


class IdentifierUnavailable(RuntimeError):
    """A configured identifier cannot be built — a bad rule, a missing model,
    a missing library. Raised at boot, never on a turn, and says what is wrong."""


class PiiIdentifier(Protocol):
    # Names the identifier in logs and in ``Redaction.failed``.
    name: str

    async def identify(self, text: str) -> list[PiiSpan]:
        """Spans found in ``text``, with offsets into it. Every span's
        ``value`` is filled; the policy decides what is kept."""
        ...
