"""The seam for the scheme catalog — implemented by an adapter.

Read once at boot and shown whole to the intent model (ADR-0021).
Synchronous: memory, not I/O.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from dss.core.intent.models import SchemeEntry


class SchemeCatalog(Protocol):
    def schemes(self) -> Sequence[SchemeEntry]:
        """Every scheme with the aliases its row lists, in file order — what
        the intent model is shown (ADR-0021)."""
        ...
