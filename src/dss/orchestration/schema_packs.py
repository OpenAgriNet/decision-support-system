"""Composition root for the schema pack cache.

Returns the cache unrefreshed — the first refresh() call, and every one
after it, is triggered from outside (startup hook, or a cron-driven
endpoint), not by construction itself.
"""

from __future__ import annotations

from pathlib import Path

from dss.adapters.schema_packs.filesystem import FilesystemSchemaPackSource
from dss.core.provider_discovery.schema_pack_cache import SchemaPackCache


def build_schema_pack_cache(network_specs_schema_path: Path) -> SchemaPackCache:
    source = FilesystemSchemaPackSource(root=network_specs_schema_path)
    return SchemaPackCache(source)
