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
import tomllib
from pathlib import Path

import httpx
import pytest

from dss.adapters.discovery.client import HttpCapabilityDiscovery
from dss.adapters.invocation.client import HttpCapabilityInvocation
from dss.adapters.schema_packs.filesystem import FilesystemSchemaPackSource
from dss.core.planner.resource_attributes import build_resource_attributes
from dss.core.provider_discovery.models import ProviderCapability, ProviderQuery
from dss.core.provider_discovery.schema_pack_cache import SchemaPackCache
from dss.core.shared.models import Geometry, Location, UserTurn
from dss.ports.invocation import SelectFailed
from tools.mock_network.app import DEFAULT_QUESTIONS, build_mock_app
from tools.mock_network.generators import (
    advisory_answer,
    mandi_answer,
    weather_answer,
)

_WEATHER = "openagrinet:WeatherObservation"
_MANDI = "openagrinet:MandiPrice"
_ADVISORY = "openagrinet:KnowledgeAdvisory"


def _benchmark_questions() -> list[dict]:
    """The raw entries the mock reads, with their written answers."""

    with DEFAULT_QUESTIONS.open("rb") as file:
        return tomllib.load(file)["question"]


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


# WeatherObservation's filterable paths. The pack also advertises
# `forecastHorizon` and `updateFrequency` — facts about the provider, not
# filters — and they are deliberately absent, so a select never sends them.
_WEATHER_FILTERABLE = (
    "informationMode",
    "subjectCategories",
    "supportedObservationTypes",
    "supportedParameters",
    "geographicGranularities",
    "observationType",
    "source.sourceId",
    "location",
    "parameters[].parameter",
)


def _planner_attributes(capability: ProviderCapability, model_filled: dict) -> dict:
    """`resourceAttributes` as the planner builds them, for a turn in Akola.

    Two steps make a select request, and only the first adds `@type`: the
    planner assembles the attributes, then the adapter wraps them in the
    envelope. Calling the adapter with a bare dict would send something no
    real turn sends, and the mock — which routes on `@type`, as the real
    network does — would rightly not recognise it.

    The turn carries its point, as the benchmark's weather questions do: the
    mock keys a weather answer on it.
    """

    return build_resource_attributes(
        capability=capability,
        subject_category="Weather",
        turn=UserTurn(
            original_query="q",
            enriched_query="q",
            source_lang="en",
            target_lang="en",
            channel="web",
            session_id="conv_1",
            transaction_id="txn_1",
            location=Location(
                area="Akola", geometry=Geometry(coordinates=[77.056016, 20.748005])
            ),
        ),
        model_filled=model_filled,
        schema_context_index={
            _WEATHER: (
                "https://openagrinet.github.io/network-specs/schema/"
                "WeatherObservation/v0.1/context.jsonld"
            )
        },
        filterable=_WEATHER_FILTERABLE,
        declared=("location",),
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

    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_mock_app(pack_dir=pack_dir)),
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


async def test_mandi_discover_advertises_each_benchmark_market(
    discovery: HttpCapabilityDiscovery,
) -> None:
    """A real mandi provider advertises one resource per market, with the
    commodities it prices there. The planner echoes them back at select, which
    is what the mock matches on — so they come from the benchmark's own
    question set, and a question can never ask for a market nobody offers."""

    result = await discovery.discover(
        ProviderQuery(
            capabilities=(_MANDI,),
            subject_category="Market",
            languages=("en",),
            coverage=None,
        ),
        ask_indices=(0,),
        transaction_id="9f2c1a8e-4b70-4d31-9c55-6f2e0b1d7a44",
    )

    by_market = {c.resource_id: c.advertised for c in result.capabilities[0]}
    assert set(by_market) == {
        f"resource:mandi-price:market:{code}"
        for code in ("9001", "9002", "9003", "9004", "9005")
    }
    mumbai = by_market["resource:mandi-price:market:9001"]
    assert mumbai["market"]["marketName"] == "Mumbai APMC"
    assert {c["name"] for c in mumbai["supportedCommodities"]} == {"Potato", "Cotton"}


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

    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_mock_app(pack_dir=pack_dir)),
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
    # and the values themselves — what the composer quotes back: the built
    # answer for this point, every parameter the pack names
    assert answer.attributes == weather_answer(lon=77.056016, lat=20.748005)
    assert len(answer.attributes["parameters"]) == 8


async def test_mandi_select_answers_one_price_for_the_advertised_market(
    discovery: HttpCapabilityDiscovery, pack_dir: Path
) -> None:
    """Through both hops, as a turn makes them: discover a market, then select
    it with the commodity the model picked. The planner echoes the advertised
    market, so the mock's answer is for exactly the market it offered."""

    found = await discovery.discover(
        ProviderQuery(
            capabilities=(_MANDI,),
            subject_category="Market",
            languages=("en",),
            coverage=None,
        ),
        ask_indices=(0,),
        transaction_id="txn-mandi",
    )
    mumbai = next(
        c
        for c in found.capabilities[0]
        if c.resource_id == "resource:mandi-price:market:9001"
    )
    attrs = build_resource_attributes(
        capability=mumbai,
        subject_category="Market",
        turn=UserTurn(
            original_query="q",
            enriched_query="q",
            source_lang="en",
            target_lang="en",
            channel="web",
            session_id="conv_1",
            transaction_id="txn-mandi",
        ),
        model_filled={"supportedCommodities": [{"code": "24"}]},
        schema_context_index={_MANDI: "https://example.test/MandiPrice/context"},
        filterable=("market", "supportedCommodities[].code"),
    )
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_mock_app(pack_dir=pack_dir)),
        base_url="http://mock.test",
    )
    invocation = HttpCapabilityInvocation(
        client=client, base_url="http://mock.test", sender_id="dss", receiver_id="x"
    )

    answer = await invocation.select(mumbai, attrs, "txn-mandi")

    assert answer.attributes == mandi_answer(
        commodity={"code": "24", "name": "Potato"},
        market=mumbai.advertised["market"],
    )


async def test_advisory_select_answers_with_the_questions_written_answer(
    pack_dir: Path,
) -> None:
    """The planner writes `topics` itself, from the question. The mock finds
    the benchmark question whose match words the topic holds and sends its
    hand-written answer, so the composer reads advice that fits the ask."""

    capability = ProviderCapability(
        provider_id="kvk-advisory-mock",
        provider_name="Krishi Vigyan Kendra Advisory Service",
        capability=_ADVISORY,
        resource_id="res:kvk:crop-advisory",
    )
    attrs = build_resource_attributes(
        capability=capability,
        subject_category="Crop",
        turn=UserTurn(
            original_query="q",
            enriched_query="q",
            source_lang="en",
            target_lang="en",
            channel="web",
            session_id="conv_1",
            transaction_id="txn-advisory",
        ),
        model_filled={"topics": ["Ginger seed rate in Sangli"]},
        schema_context_index={_ADVISORY: "https://example.test/Advisory/context"},
        filterable=("topics",),
    )
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_mock_app(pack_dir=pack_dir)),
        base_url="http://mock.test",
    )
    invocation = HttpCapabilityInvocation(
        client=client, base_url="http://mock.test", sender_id="dss", receiver_id="x"
    )

    answer = await invocation.select(capability, attrs, "txn-advisory")

    ginger = next(q for q in _benchmark_questions() if q["id"] == "7-1")
    assert answer.attributes == advisory_answer(ginger["answer"])


async def test_a_select_the_mock_cannot_answer_is_refused_and_counted(
    pack_dir: Path,
) -> None:
    """A miss must show, never be answered with the wrong data: the benchmark
    counts it and leaves the turn out of its figures. The transaction id says
    which turn missed, even with several turns in flight."""

    app = build_mock_app(pack_dir=pack_dir)
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://mock.test"
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
    )
    attrs = _planner_attributes(capability, {})
    no_location = {k: v for k, v in attrs.items() if k != "location"}

    with pytest.raises(SelectFailed) as failed:
        await invocation.select(capability, no_location, "txn-miss")

    assert failed.value.status_code == 400
    misses = (await client.get("/_bench/misses")).json()
    assert misses == {"count": 1, "transactionIds": ["txn-miss"]}


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
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_mock_app(pack_dir=pack_dir)),
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
