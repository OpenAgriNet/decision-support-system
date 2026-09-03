"""Holds the capability index, rebuilt on demand.

No internal timer: startup and periodic refresh both just call refresh().
Scheduling when that happens belongs outside core.
"""

from __future__ import annotations

from dss.core.provider_discovery.index import (
    build_capability_index,
    build_schema_context_index,
)
from dss.core.provider_discovery.models import SchemaPackFiles
from dss.ports.schema_packs import SchemaPackSource


class SchemaPackCache:
    def __init__(self, source: SchemaPackSource) -> None:
        self._source = source
        self._index: dict[tuple[str, str], tuple[str, ...]] = {}
        self._schema_context_index: dict[str, tuple[str, str]] = {}

    def current(self) -> dict[tuple[str, str], tuple[str, ...]]:
        return self._index

    def current_schema_context(self) -> dict[str, tuple[str, str]]:
        return self._schema_context_index

    async def refresh(self) -> None:
        packs: tuple[SchemaPackFiles, ...] = await self._source.fetch_packs()
        self._index = build_capability_index(packs)
        self._schema_context_index = build_schema_context_index(packs)
