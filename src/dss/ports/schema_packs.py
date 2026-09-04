"""The seam for fetching schema packs — implemented by an adapter."""

from __future__ import annotations

from typing import Protocol

from dss.core.provider_discovery.models import SchemaPackFiles


class SchemaPackSource(Protocol):
    async def fetch_packs(self) -> tuple[SchemaPackFiles, ...]: ...
