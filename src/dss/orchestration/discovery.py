"""Composition root for the discovery client adapter."""

from __future__ import annotations

import httpx2

from dss.adapters.discovery.client import HttpCapabilityDiscovery, SchemaContextSource


def build_capability_discovery(
    client: httpx2.AsyncClient,
    base_url: str,
    schema_pack_cache: SchemaContextSource,
    schema_base_url: str,
) -> HttpCapabilityDiscovery:
    return HttpCapabilityDiscovery(
        client=client,
        base_url=base_url,
        schema_pack_cache=schema_pack_cache,
        schema_base_url=schema_base_url,
    )
