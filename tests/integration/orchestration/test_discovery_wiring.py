"""End-to-end: a query resolves through the wired HttpCapabilityDiscovery."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx2

from dss.adapters.schema_packs.filesystem import FilesystemSchemaPackSource
from dss.core.intent.models import Ask, Intent, InteractionType, SubjectCategory
from dss.core.provider_discovery.models import ProviderQuery
from dss.core.provider_discovery.schema_pack_cache import SchemaPackCache
from dss.core.shared.models import UserTurn
from dss.orchestration.discovery import (
    build_capability_discovery,
    build_discover_providers,
)
from dss.orchestration.turn import run_turn

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

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=ON_DISCOVER_RESPONSE)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        discovery = build_capability_discovery(
            client=client,
            base_url="https://discovery-network-vistaar.da.gov.in/oan",
            schema_pack_cache=schema_pack_cache,
        )
        query = ProviderQuery(
            capabilities=("openagrinet:MandiPrice",),
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

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=ON_DISCOVER_RESPONSE)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        discovery = build_capability_discovery(
            client=client,
            base_url="https://discovery-network-vistaar.da.gov.in/oan",
            schema_pack_cache=schema_pack_cache,
        )
        discover_providers = build_discover_providers(
            discovery=discovery, schema_pack_cache=schema_pack_cache, radius_m=25000
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

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=ON_DISCOVER_RESPONSE)

    cache = SchemaPackCache(FilesystemSchemaPackSource(root=SCHEMA_PACKS_FIXTURE_ROOT))
    await cache.refresh()

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        discover_providers = build_discover_providers(
            build_capability_discovery(
                client=client,
                base_url="https://network-adapter.example",
                schema_pack_cache=cache,
            ),
            cache,
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
