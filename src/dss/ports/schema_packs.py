"""The seam for fetching schema packs — implemented by an adapter."""

from __future__ import annotations

from typing import Protocol

from dss.core.provider_discovery.models import SchemaPackFiles, SchemaPackSkipped


class SchemaPackSource(Protocol):
    async def fetch_packs(self) -> tuple[SchemaPackFiles, ...]: ...

    def skipped_packs(self) -> tuple[SchemaPackSkipped, ...]:
        """Packs the source itself could not read — before a pack reaches
        `fetch_packs`'s result, so a caller building an index over that
        result would otherwise never see them."""
        ...
