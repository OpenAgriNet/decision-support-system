"""The seam for the scheme catalog — implemented by an adapter.

The index is pre-built and normalized at boot, so the resolver stays a lookup
and a catalog of any size costs nothing per turn. Synchronous: memory, not I/O.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from dss.core.enrichment.models import Scheme


class SchemeCatalog(Protocol):
    def aliases(self) -> Mapping[str, Scheme]:
        """Every alias, keyed by `core.enrichment.normalize.alias_key`.

        Includes each scheme's official name as an alias of itself, whether or
        not the catalog's author repeated it in the alias column.
        """
        ...
