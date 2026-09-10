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

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest

from dss.adapters.discovery.client import HttpCapabilityDiscovery
from dss.adapters.invocation.client import HttpCapabilityInvocation
from dss.adapters.schema_packs.filesystem import FilesystemSchemaPackSource
from dss.core.planner.resource_attributes import build_resource_attributes
from dss.core.provider_discovery.models import ProviderCapability, ProviderQuery
from dss.core.provider_discovery.schema_pack_cache import SchemaPackCache
from dss.core.shared.models import UserTurn
from tools.mock_network.app import build_mock_app

_WEATHER = "openagrinet:WeatherObservation"
_MANDI = "openagrinet:MandiPrice"

_PROFILE = '{"filterable_paths": ["beckn:resourceAttributes.observationType"]}'


def _attributes_yaml(pack: str) -> str:
    """A pack declaring just what the discovery adapter reads from one.

    `x-jsonld` (for the `@context` the request carries) plus
    `informationMode`, which is what makes a resource a capability rather than
    a Direct answer.
    """

    return f"""
components:
  schemas:
    {pack}:
      type: object
      x-beckn-container: resourceAttributes
      x-jsonld:
        "@context": "https://openagrinet.github.io/network-specs/schema/{pack}/v0.1/context.jsonld"
        "@type": openagrinet:{pack}
      allOf:
        - type: object
          required: [informationMode]
          properties:
            informationMode:
              type: string
              enum: [OnDemand, Direct]
"""


def _write_pack(root: Path, pack: str, category: str) -> None:
    version = root / pack / "v0.1"
    (version / "examples").mkdir(parents=True)
    (version / "attributes.yaml").write_text(_attributes_yaml(pack), encoding="utf-8")
    (version / "profile.json").write_text(_PROFILE, encoding="utf-8")
    (version / "examples" / "sample.json").write_text(
        json.dumps({"subjectCategories": [category]}), encoding="utf-8"
    )


def _planner_attributes(capability: ProviderCapability, model_filled: dict) -> dict:
    """`resourceAttributes` as the planner builds them.

    Two steps make a select request, and only the first adds `@type`: the
    planner assembles the attributes, then the adapter wraps them in the
    envelope. Calling the adapter with a bare dict would send something no
    real turn sends, and the mock — which routes on `@type`, as the real
    network does — would rightly not recognise it.
    """

    return build_resource_attributes(
        capability=capability,
        turn=UserTurn(
            original_query="q",
            enriched_query="q",
            source_lang="en",
            target_lang="en",
            channel="web",
            session_id="conv_1",
            transaction_id="txn_1",
        ),
        model_filled=model_filled,
        schema_context_index={
            _WEATHER: (
                "https://openagrinet.github.io/network-specs/schema/"
                "WeatherObservation/v0.1/context.jsonld"
            )
        },
    )


@pytest.fixture
def pack_dir(tmp_path: Path) -> Path:
    """A pack directory of the shape `DSS_SCHEMA_PACK_DIR` points at.

    Both packs the mock serves, so one running mock can be asked for either —
    which is the point of keying scenarios off `@type`.
    """

    _write_pack(tmp_path, "WeatherObservation", "Weather")
    _write_pack(tmp_path, "MandiPrice", "Market")
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
        ProviderQuery(
            capabilities=(_WEATHER,),
            subject_category="Weather",
            languages=("en",),
            coverage=None,
        ),
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


async def test_one_running_mock_answers_either_capability(
    discovery: HttpCapabilityDiscovery,
) -> None:
    """Weather and mandi from the same process, no restart between them.

    This is why scenarios are keyed off `@type` rather than a mode flag: two
    questions in one session pick different providers, and a mock needing a
    restart between them could not serve a turn that asks about both.
    """

    async def provider_for(capability: str) -> str:
        result = await discovery.discover(
            ProviderQuery(
                capabilities=(capability,),
                subject_category="Weather",
                languages=("en",),
                coverage=None,
            ),
            ask_indices=(0,),
            transaction_id="9f2c1a8e-4b70-4d31-9c55-6f2e0b1d7a44",
        )
        return result.capabilities[0][0].provider_id

    assert await provider_for(_WEATHER) == "mausamgram-mock"
    assert await provider_for(_MANDI) == "agmarknet-mock"


async def test_a_query_naming_two_capabilities_gets_both(
    discovery: HttpCapabilityDiscovery,
) -> None:
    """One ask can resolve to two `@type`s, and both must come back.

    `('Crop', 'Knowledge')` does exactly this against the real packs — it maps
    to AgricultureResource and KnowledgeAdvisory — so the DSS ORs them into
    one predicate. Answering only the first would make the turn partially
    answered for no reason a log would explain.
    """

    result = await discovery.discover(
        ProviderQuery(
            capabilities=(_WEATHER, _MANDI),
            subject_category="Weather",
            languages=("en",),
            coverage=None,
        ),
        ask_indices=(0,),
        transaction_id="9f2c1a8e-4b70-4d31-9c55-6f2e0b1d7a44",
    )

    assert {c.provider_id for c in result.capabilities[0]} == {
        "mausamgram-mock",
        "agmarknet-mock",
    }


async def test_select_returns_the_values_the_composer_will_quote(
    pack_dir: Path,
) -> None:
    """The second hop: the planner selects the capability and gets values.

    Asserted through the real invocation adapter, because that is what decides
    whether the payload becomes an answer. `attributes` is what reaches the
    composer — so a body that arrives but maps to nothing would pass a looser
    test and produce an empty answer.
    """

    client = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=build_mock_app(pack_dir=pack_dir)),
        base_url="http://mock.test",
    )
    invocation = HttpCapabilityInvocation(
        client=client,
        base_url="http://mock.test",
        sender_id="dss",
        receiver_id="oan-mock",
    )
    capability = ProviderCapability(
        provider_id="mausamgram-mock",
        provider_name="IMD Mausamgram NWP",
        capability=_WEATHER,
        resource_id="res:mausamgram:point-forecast",
        observed_categories=("Weather",),
        provider_code="IMD-NWP-01",
    )

    answer = await invocation.select(
        capability,
        _planner_attributes(capability, {"observationType": "Forecast"}),
        "9f2c1a8e-4b70-4d31-9c55-6f2e0b1d7a44",
    )

    # provenance the answer must carry, from the request side not the response
    assert answer.provider_id == "mausamgram-mock"
    assert answer.provider_name == "IMD Mausamgram NWP"
    # the provider's own resource id, not the uuid4 the DSS sent
    assert answer.resource_id == "res:mausamgram:forecast:point"
    # and the values themselves — what the composer quotes back
    rainfall = next(
        p for p in answer.attributes["parameters"] if p["parameter"] == "Rainfall"
    )
    assert rainfall["values"]["sum"] == 5.2
    assert rainfall["unit"] == "mm"


async def test_the_selected_answer_is_valid_now_not_when_recorded(
    pack_dir: Path,
) -> None:
    """The recorded example's window ended 2026-08-24; this one covers now.

    Under OnDemand this does not decide whether the answer survives — the
    expiry filter runs in `discover_providers` and applies to Direct answers
    only. It decides whether the answer is *honest*: a forecast labelled with
    last month's window read as current would be the mock lying.
    """

    client = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=build_mock_app(pack_dir=pack_dir)),
        base_url="http://mock.test",
    )
    invocation = HttpCapabilityInvocation(
        client=client,
        base_url="http://mock.test",
        sender_id="dss",
        receiver_id="oan-mock",
    )
    capability = ProviderCapability(
        provider_id="mausamgram-mock",
        provider_name="IMD Mausamgram NWP",
        capability=_WEATHER,
        resource_id="res:mausamgram:point-forecast",
        observed_categories=("Weather",),
        provider_code="IMD-NWP-01",
    )

    answer = await invocation.select(
        capability, _planner_attributes(capability, {}), "txn"
    )

    assert answer.validity is not None
    now = datetime.now(UTC)
    assert answer.validity.starts_at is not None
    assert answer.validity.ends_at is not None
    assert answer.validity.starts_at <= now <= answer.validity.ends_at


async def test_a_type_the_mock_does_not_serve_finds_nobody(
    pack_dir: Path,
) -> None:
    """A `@type` the DSS can ask for but the mock has no scenario for.

    An empty catalog, not an error — what the real network reports when no
    provider matches, and the path a `no_match` turn takes.

    `AgricultureResource` is the real case: it is the shared-fields base every
    pack `$ref`s, not a capability a provider advertises, but the capability
    index registers it anyway (see TODO.md) so a Crop question asks for it. The
    mock has nothing to answer with, which is what the network would say too.

    The pack has to be present for this to be reachable at all: the request
    builder reads `@context` from the schema-context index, so a `@type` the
    DSS does not know about raises there and never reaches the wire.
    """

    _write_pack(pack_dir, "AgricultureResource", "Crop")

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
            capabilities=("openagrinet:AgricultureResource",),
            subject_category="Weather",
            languages=("en",),
            coverage=None,
        ),
        ask_indices=(0,),
        transaction_id="9f2c1a8e-4b70-4d31-9c55-6f2e0b1d7a44",
    )

    assert result.capabilities == {0: ()}
    assert result.answers == {0: ()}
    assert result.failures == {0: ()}
