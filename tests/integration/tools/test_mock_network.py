"""Tier 2 — the mock network over a real socket.

Asserted through the DSS's own adapter, not by reading the mock's JSON: what
matters is that `HttpCapabilityDiscovery` maps the response into a capability,
because that is what a turn depends on. A test checking the mock's own output
would pass while the DSS silently dropped every resource — which is what
happens when `informationMode` is missing.

The mock reads the same `DSS_SCHEMA_PACK_DIR` the DSS reads, so it can only
advertise a `@type` the DSS can route. Here that directory is built in
`tmp_path` from the packs' real `x-jsonld`, so no `@context` URL is written by
hand.
"""

from __future__ import annotations

from pathlib import Path

import httpx2
import pytest

from dss.adapters.discovery.client import HttpCapabilityDiscovery
from dss.adapters.schema_packs.filesystem import FilesystemSchemaPackSource
from dss.core.provider_discovery.models import ProviderQuery
from dss.core.provider_discovery.schema_pack_cache import SchemaPackCache
from tools.mock_network.app import build_mock_app

_WEATHER = "openagrinet:WeatherObservation"

# The pack the mock's weather scenario needs, at its real advertised @context.
_ATTRIBUTES = """
components:
  schemas:
    WeatherObservation:
      type: object
      x-beckn-container: resourceAttributes
      x-jsonld:
        "@context": "https://openagrinet.github.io/network-specs/schema/WeatherObservation/v0.1/context.jsonld"
        "@type": openagrinet:WeatherObservation
      allOf:
        - type: object
          required: [informationMode]
          properties:
            informationMode:
              type: string
              enum: [OnDemand, Direct]
"""

_PROFILE = '{"filterable_paths": ["beckn:resourceAttributes.observationType"]}'
_EXAMPLE = '{"subjectCategories": ["Weather"]}'


@pytest.fixture
def pack_dir(tmp_path: Path) -> Path:
    """A pack directory of the shape `DSS_SCHEMA_PACK_DIR` points at."""

    version = tmp_path / "WeatherObservation" / "v0.1"
    (version / "examples").mkdir(parents=True)
    (version / "attributes.yaml").write_text(_ATTRIBUTES, encoding="utf-8")
    (version / "profile.json").write_text(_PROFILE, encoding="utf-8")
    (version / "examples" / "forecast.json").write_text(_EXAMPLE, encoding="utf-8")
    return tmp_path


@pytest.fixture
async def discovery(pack_dir: Path) -> HttpCapabilityDiscovery:
    """The real discovery adapter, talking to the mock in-process.

    `ASGITransport` runs the mock app with no port. The separate process is for
    running it by hand; here the same app object serves the test, so there is
    one body of mock behaviour rather than two that can disagree.

    A real `SchemaPackCache` over real pack files, not a hand-written index:
    `@context` is whatever the pack declares, read verbatim, so there is
    nothing here to keep in sync with `build_schema_context_index`.
    """

    cache = SchemaPackCache(FilesystemSchemaPackSource(root=pack_dir))
    await cache.refresh()

    client = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=build_mock_app(pack_dir=pack_dir)),
        base_url="http://mock.test",
    )
    return HttpCapabilityDiscovery(
        client=client, base_url="http://mock.test", schema_pack_cache=cache
    )


async def test_discover_returns_a_capability_the_adapter_maps(
    discovery: HttpCapabilityDiscovery,
) -> None:
    """The whole point: a provider the planner can then `/select`.

    `informationMode: OnDemand` is what makes it a capability rather than a
    Direct answer, and a resource missing it is dropped without a word — so
    this asserts the mapped result, not the response body.
    """

    result = await discovery.discover(
        ProviderQuery(capabilities=(_WEATHER,), languages=("en",), coverage=None),
        ask_indices=(0,),
        transaction_id="9f2c1a8e-4b70-4d31-9c55-6f2e0b1d7a44",
    )

    assert result.failures == {0: ()}
    capabilities = result.capabilities[0]
    assert len(capabilities) == 1
    capability = capabilities[0]
    assert capability.capability == _WEATHER
    assert capability.provider_id == "mausamgram-mock"
    assert capability.provider_name == "IMD Mausamgram NWP"
    assert capability.resource_id == "res:mausamgram:point-forecast"
    assert capability.observed_categories == ("Weather",)


async def test_a_type_the_mock_does_not_serve_finds_nobody(
    pack_dir: Path,
) -> None:
    """A `@type` the DSS can ask for but the mock has no scenario for.

    An empty catalog, not an error — what the real network reports when no
    provider matches, and the path a `no_match` turn takes.

    The pack has to be present for this to be reachable at all: the request
    builder reads `@context` from the schema-context index, so a `@type` the
    DSS does not know about raises there and never reaches the wire. So this
    adds a second pack the mock deliberately does not serve.
    """

    mandi = pack_dir / "MandiPrice" / "v0.1"
    (mandi / "examples").mkdir(parents=True)
    (mandi / "attributes.yaml").write_text(
        _ATTRIBUTES.replace("WeatherObservation", "MandiPrice"), encoding="utf-8"
    )
    (mandi / "profile.json").write_text(_PROFILE, encoding="utf-8")
    (mandi / "examples" / "onion.json").write_text(
        '{"subjectCategories": ["Market"]}', encoding="utf-8"
    )

    cache = SchemaPackCache(FilesystemSchemaPackSource(root=pack_dir))
    await cache.refresh()
    client = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=build_mock_app(pack_dir=pack_dir)),
        base_url="http://mock.test",
    )
    discovery = HttpCapabilityDiscovery(
        client=client, base_url="http://mock.test", schema_pack_cache=cache
    )

    result = await discovery.discover(
        ProviderQuery(
            capabilities=("openagrinet:MandiPrice",), languages=("en",), coverage=None
        ),
        ask_indices=(0,),
        transaction_id="9f2c1a8e-4b70-4d31-9c55-6f2e0b1d7a44",
    )

    assert result.capabilities == {0: ()}
    assert result.answers == {0: ()}
    assert result.failures == {0: ()}
