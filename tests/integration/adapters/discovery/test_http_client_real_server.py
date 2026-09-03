"""HttpCapabilityDiscovery against a real local HTTP server.

Complements test_http_client.py's MockTransport tests: this proves an
actual socket-level round trip works (request serialization, response
parsing over real HTTP) — something an in-process mock transport cannot
prove, since it never leaves the process.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx2
import pytest
from pytest_httpserver import HTTPServer

from dss.adapters.discovery.client import HttpCapabilityDiscovery
from dss.core.provider_discovery.models import ProviderQuery

FIXTURES = Path(__file__).parent / "fixtures"


class _FakeSchemaPackCache:
    def __init__(self, schema_context_index: dict[str, tuple[str, str]]) -> None:
        self._schema_context_index = schema_context_index

    def current_schema_context(self) -> dict[str, tuple[str, str]]:
        return self._schema_context_index


SCHEMA_PACK_CACHE = _FakeSchemaPackCache(
    {"openagrinet:WeatherObservation": ("WeatherObservation", "v0.1")}
)


@pytest.mark.anyio
async def test_discover_against_a_real_local_server(httpserver: HTTPServer) -> None:
    on_discover = json.loads((FIXTURES / "on_discover_response.json").read_text())
    httpserver.expect_request("/discover", method="POST").respond_with_json(on_discover)

    async with httpx2.AsyncClient() as client:
        discovery = HttpCapabilityDiscovery(
            client=client,
            base_url=httpserver.url_for(""),
            schema_pack_cache=SCHEMA_PACK_CACHE,
            schema_base_url="https://schemas.openagrinet.global/schema",
        )
        query = ProviderQuery(
            capabilities=("openagrinet:WeatherObservation",),
            languages=("hi",),
            coverage=None,
        )

        result = await discovery.discover(query, ask_indices=(0,))

    assert result.capabilities[0][0].provider_id == "mausamgram"


@pytest.mark.anyio
async def test_a_real_429_response_raises(httpserver: HTTPServer) -> None:
    httpserver.expect_request("/discover", method="POST").respond_with_json(
        {"error": "rate limited"}, status=429
    )

    async with httpx2.AsyncClient() as client:
        discovery = HttpCapabilityDiscovery(
            client=client,
            base_url=httpserver.url_for(""),
            schema_pack_cache=SCHEMA_PACK_CACHE,
            schema_base_url="https://schemas.openagrinet.global/schema",
        )
        query = ProviderQuery(
            capabilities=("openagrinet:WeatherObservation",),
            languages=("hi",),
            coverage=None,
        )

        with pytest.raises(httpx2.HTTPStatusError) as exc_info:
            await discovery.discover(query, ask_indices=(0,))

    assert exc_info.value.response.status_code == 429
