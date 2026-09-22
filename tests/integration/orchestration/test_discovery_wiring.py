"""End-to-end: a query resolves through the wired HttpCapabilityDiscovery."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from dss.adapters.observability.tracing import open_span
from dss.adapters.schema_packs.filesystem import FilesystemSchemaPackSource
from dss.core.intent.models import Ask, Intent, InteractionType, SubjectCategory
from dss.core.provider_discovery.models import ProviderQuery
from dss.core.provider_discovery.schema_pack_cache import SchemaPackCache
from dss.core.shared.models import Geometry, Location, UserTurn
from dss.observability.trace_log import set_stage_span_opener, trace_component
from dss.orchestration.discovery import (
    build_capability_discovery,
    build_discover_providers,
)
from dss.orchestration.turn import run_turn
from tests.support.fakes import FakeAreaLookup

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
        / "discover_response_direct.json"
    ).read_text()
)


async def test_the_wired_adapter_resolves_a_query() -> None:
    schema_pack_cache = SchemaPackCache(
        FilesystemSchemaPackSource(root=SCHEMA_PACKS_FIXTURE_ROOT)
    )
    await schema_pack_cache.refresh()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=ON_DISCOVER_RESPONSE)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        discovery = build_capability_discovery(
            client=client,
            base_url="https://discovery-network-vistaar.da.gov.in/oan",
            schema_pack_cache=schema_pack_cache,
        )
        query = ProviderQuery(
            capabilities=("openagrinet:MandiPrice",),
            subject_category="Market",
            languages=("hi",),
            coverage=None,
        )

        result = await discovery.discover(query, ask_indices=(0,), transaction_id="t1")

    assert result.answers[0][0].provider_id == "agmarknet"


async def test_the_wired_discover_providers_bakes_in_radius() -> None:
    schema_pack_cache = SchemaPackCache(
        FilesystemSchemaPackSource(root=SCHEMA_PACKS_FIXTURE_ROOT)
    )
    await schema_pack_cache.refresh()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=ON_DISCOVER_RESPONSE)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        discovery = build_capability_discovery(
            client=client,
            base_url="https://discovery-network-vistaar.da.gov.in/oan",
            schema_pack_cache=schema_pack_cache,
        )
        discover_providers = build_discover_providers(
            discovery=discovery,
            schema_pack_cache=schema_pack_cache,
            area_lookup=FakeAreaLookup(),
            radius_m=25000,
        )
        ask = Ask(
            subject_categories=SubjectCategory.MARKET,
            interaction_type=InteractionType.OBSERVE,
        )
        intent = Intent(asks=(ask,), confidence=0.9)
        turn = UserTurn(
            original_query="onion price",
            enriched_query="onion price",
            session_id="s1",
            transaction_id="t1",
            source_lang="hi",
            target_lang="hi",
            channel="web",
        )

        result = await discover_providers(
            intent, turn, now=datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)
        )

    assert result.answers[0][0].provider_id == "agmarknet"


async def test_the_fan_out_runs_inside_a_span(monkeypatch) -> None:
    """One span for the whole fan-out, opened out here.

    The slot tasks are spawned inside `core/`, which may not open spans
    (ADR-0012), so per-slot detail would need a port. One span around the
    fan-out is what orchestration can see, and the per-provider `dss.discover`
    spans underneath already show a single slow provider.
    """

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr("opentelemetry.trace.get_tracer_provider", lambda: provider)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=ON_DISCOVER_RESPONSE)

    cache = SchemaPackCache(FilesystemSchemaPackSource(root=SCHEMA_PACKS_FIXTURE_ROOT))
    await cache.refresh()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        discover_providers = build_discover_providers(
            build_capability_discovery(
                client=client,
                base_url="https://network-adapter.example",
                schema_pack_cache=cache,
            ),
            cache,
            FakeAreaLookup(),
            50_000,
        )
        ask = Ask(
            subject_categories=SubjectCategory.MARKET,
            interaction_type=InteractionType.OBSERVE,
        )
        turn = UserTurn(
            original_query="onion price",
            enriched_query="onion price",
            session_id="s1",
            transaction_id="t1",
            source_lang="hi",
            target_lang="hi",
            channel="web",
        )

        # `trace_component` and the real opener, as `turn.py` does it — a
        # hand-rolled span here would not prove the two nest.
        set_stage_span_opener(open_span)
        try:
            with trace_component("discovery", "t1"):
                await discover_providers(
                    Intent(asks=(ask,), confidence=0.9),
                    turn,
                    now=datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC),
                )
        finally:
            set_stage_span_opener(None)

    names = [span.name for span in exporter.get_finished_spans()]
    assert "dss.stage.discovery" in names
    # No wrapper level of its own. `trace_component("discovery")` in `turn.py`
    # brackets exactly this call, so a span here would start and end with it —
    # the empty nesting ADR-0012 rejects for `dss.plan_execution`.
    assert "dss.provider_discovery" not in names


async def test_the_fan_out_span_counts_how_many_asks_went_unanswered(
    monkeypatch,
) -> None:
    """One provider down does not make the turn fail, so this span stays green
    — but the trace has to say so somewhere. A count, because OpenTelemetry
    status is only OK or ERROR and "1 of 2 answered" is neither.

    Counted per ask rather than per provider, because that is what a
    `DiscoveryResult` is keyed by. The failed call's own `dss.discover` child
    is the red one; this span is about the fan-out, which did its job.
    """

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr("opentelemetry.trace.get_tracer_provider", lambda: provider)

    def unavailable(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "simulated"})

    cache = SchemaPackCache(FilesystemSchemaPackSource(root=SCHEMA_PACKS_FIXTURE_ROOT))
    await cache.refresh()

    async with httpx.AsyncClient(transport=httpx.MockTransport(unavailable)) as client:
        discover_providers = build_discover_providers(
            build_capability_discovery(
                client=client,
                base_url="https://network-adapter.example",
                schema_pack_cache=cache,
            ),
            cache,
            FakeAreaLookup(),
            50_000,
        )
        ask = Ask(
            subject_categories=SubjectCategory.MARKET,
            interaction_type=InteractionType.OBSERVE,
        )
        turn = UserTurn(
            original_query="onion price",
            enriched_query="onion price",
            session_id="s1",
            transaction_id="t1",
            source_lang="hi",
            target_lang="hi",
            channel="web",
        )

        set_stage_span_opener(open_span)
        try:
            with trace_component("discovery", "t1"):
                await discover_providers(
                    Intent(asks=(ask,), confidence=0.9),
                    turn,
                    now=datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC),
                )
        finally:
            set_stage_span_opener(None)

    (stage,) = [
        s for s in exporter.get_finished_spans() if s.name == "dss.stage.discovery"
    ]
    assert stage.attributes["asks_total"] == 1
    assert stage.attributes["asks_failed"] == 1
    assert stage.status.status_code is not StatusCode.ERROR


class _FakeIntentLLM:
    def __init__(self, result: Intent) -> None:
        self._result = result

    async def structured(self, *, system_prompt, user_query, schema):
        return self._result


class _FakeModerationLLM:
    async def structured(self, *, system_prompt, user_query, schema):
        return schema(violated_policy_id=None)


async def test_run_turn_can_call_the_composed_discover_providers() -> None:
    """The two halves of this seam, composed.

    ``test_turn.py`` substitutes a fake for ``discover_providers``, and the
    test above calls the real partial with ``now=`` as a keyword. Neither
    exercises what ``run_turn`` actually does — call the partial with three
    positional arguments — so a parameter-order mismatch between the two
    passed both suites while failing every real turn.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=ON_DISCOVER_RESPONSE)

    cache = SchemaPackCache(FilesystemSchemaPackSource(root=SCHEMA_PACKS_FIXTURE_ROOT))
    await cache.refresh()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        discover_providers = build_discover_providers(
            build_capability_discovery(
                client=client,
                base_url="https://network-adapter.example",
                schema_pack_cache=cache,
            ),
            cache,
            FakeAreaLookup(),
            50_000,
        )

        result = await run_turn(
            UserTurn(
                original_query="onion price",
                enriched_query="onion price",
                session_id="s1",
                transaction_id="t1",
                source_lang="en",
                target_lang="en",
                channel="web",
                # Located, so discovery actually runs: this test is about the
                # wired adapter being reached, not about the district question.
                location=Location(
                    geometry=Geometry(coordinates=[74.067998, 18.571118])
                ),
            ),
            intent_llm=_FakeIntentLLM(
                Intent(
                    asks=(
                        Ask(
                            subject_categories=SubjectCategory.MARKET,
                            interaction_type=InteractionType.OBSERVE,
                        ),
                    ),
                    confidence=0.9,
                )
            ),
            moderation_llm=_FakeModerationLLM(),
            policies=[],
            discover_providers=discover_providers,
        )

    assert result.discovery.answers[0][0].provider_id == "agmarknet"
