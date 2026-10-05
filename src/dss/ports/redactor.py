"""The seam for redacting a turn's texts — implemented by an adapter.

Today the adapter runs in the process (`adapters/redaction/local.py`). A
redaction service reached over HTTP would be a second adapter with the same
shape, and nothing that calls this would change.

Whatever implements it hands back the texts with tags in place, the map from
tag to real value for the one provider call that may need it, and counts per
entity. A remote implementation therefore returns real values over the wire,
so it must be reached over a protected connection.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from dss.core.redaction.service import Redaction


class Redactor(Protocol):
    async def redact(self, texts: Sequence[str]) -> Redaction:
        """Redact ``texts`` together, so a value repeated across them gets one
        tag. Returns them in the same order."""
        ...
