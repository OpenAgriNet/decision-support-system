"""Holds the capability index, rebuilt on demand.

No internal timer: startup and periodic refresh both just call refresh().
Scheduling when that happens belongs outside core.
"""

from __future__ import annotations

from dss.core.provider_discovery.index import build_capability_index
from dss.core.provider_discovery.models import SchemaPackFiles
from dss.ports.schema_packs import SchemaPackSource


class SchemaPackCache:
    def __init__(self, source: SchemaPackSource) -> None:
        self._source = source
        self._index: dict[tuple[str, str], tuple[str, ...]] = {}

    def current(self) -> dict[tuple[str, str], tuple[str, ...]]:
        return self._index

    async def refresh(self) -> None:
        packs: tuple[SchemaPackFiles, ...] = await self._source.fetch_packs()
        self._index = build_capability_index(packs)
