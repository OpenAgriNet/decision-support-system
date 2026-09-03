"""End-to-end: a query resolves through the wired HttpCapabilityDiscovery."""

from __future__ import annotations

import json
from pathlib import Path

import httpx2
import pytest

from dss.adapters.schema_packs.filesystem import FilesystemSchemaPackSource
from dss.core.provider_discovery.models import ProviderQuery
from dss.core.provider_discovery.schema_pack_cache import SchemaPackCache
from dss.orchestration.discovery import build_capability_discovery

SCHEMA_PACKS_FIXTURE_ROOT = (
    Path(__file__).parents[1]
    / "adapters"
    / "schema_packs"
    / "fixtures"
    / "network-specs"
    / "schema"
)
ON_DISCOVER_RESPONSE = json.loads(
    (
        Path(__file__).parents[1]
        / "adapters"
        / "discovery"
        / "fixtures"
        / "on_discover_response_direct.json"
    ).read_text()
)


@pytest.mark.anyio
async def test_the_wired_adapter_resolves_a_query() -> None:
    schema_pack_cache = SchemaPackCache(
        FilesystemSchemaPackSource(root=SCHEMA_PACKS_FIXTURE_ROOT)
    )
    await schema_pack_cache.refresh()

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=ON_DISCOVER_RESPONSE)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        discovery = build_capability_discovery(
            client=client,
            base_url="https://discovery-network-vistaar.da.gov.in/oan",
            schema_pack_cache=schema_pack_cache,
            schema_base_url="https://schemas.openagrinet.global/schema",
        )
        query = ProviderQuery(
            capabilities=("openagrinet:MandiPrice",),
            languages=("hi",),
            coverage=None,
        )

        result = await discovery.discover(query, ask_indices=(0,))

    assert result.answers[0][0].provider_id == "agmarknet"
