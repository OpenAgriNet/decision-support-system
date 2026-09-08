"""Holds the capability index, rebuilt on demand.

No internal timer: startup and periodic refresh both just call refresh().
Scheduling when that happens belongs outside core.
"""

from __future__ import annotations

from dss.core.provider_discovery.index import (
    build_capability_index,
    build_schema_context_index,
)
from dss.core.provider_discovery.models import SchemaPackFiles, SchemaPackSkipped
from dss.ports.schema_packs import SchemaPackSource


class SchemaPackCache:
    def __init__(self, source: SchemaPackSource) -> None:
        self._source = source
        self._index: dict[tuple[str, str], tuple[str, ...]] = {}
        self._schema_context_index: dict[str, str] = {}
        self._skipped: tuple[SchemaPackSkipped, ...] = ()

    def current(self) -> dict[tuple[str, str], tuple[str, ...]]:
        return self._index

    def current_schema_context(self) -> dict[str, str]:
        return self._schema_context_index

    def skipped_packs(self) -> tuple[SchemaPackSkipped, ...]:
        """Packs left out of the current index because they were malformed.

        Describes the current index, not history: a refresh that reads clean
        packs clears this. A non-empty result means capabilities are missing
        and should reach an operator — see SchemaPackSkipped.
        """
        return self._skipped

    async def refresh(self) -> None:
        packs: tuple[SchemaPackFiles, ...] = await self._source.fetch_packs()

        # A pack either builds into both indexes or neither: a @type present
        # in one but not the other would resolve to a request we can't build a
        # schemaContext URL for. So collect every skip first, then build both
        # from what's left.
        _, index_skips = build_capability_index(packs)
        _, context_skips = build_schema_context_index(packs)
        skipped = {skip.pack_name: skip for skip in (*context_skips, *index_skips)}
        good = tuple(pack for pack in packs if pack.pack_name not in skipped)

        index, _ = build_capability_index(good)
        context_index, _ = build_schema_context_index(good)

        # Assigned together, after both builds succeed, so a raise leaves the
        # last good pair in place rather than a new index against a stale one.
        self._index = index
        self._schema_context_index = context_index
        self._skipped = tuple(skipped.values())
