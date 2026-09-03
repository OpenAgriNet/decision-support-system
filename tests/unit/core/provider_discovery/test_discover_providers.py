"""Tests for discover_providers — ties resolution, the schema-pack index, and
CapabilityDiscovery together.
"""

from __future__ import annotations

from dss.core.intent.models import Ask, Intent, InteractionType, SubjectCategory
from dss.core.provider_discovery.models import (
    CapabilityUnresolved,
    DiscoveryResult,
    ProviderCapability,
)
from dss.core.provider_discovery.service import discover_providers
from dss.core.shared.models import UserTurn


def _turn(**overrides) -> UserTurn:
    defaults = dict(
        original_query="market price",
        enriched_query="market price",
        session_id="s1",
        source_lang="hi",
        target_lang="hi",
        channel="web",
    )
    defaults.update(overrides)
    return UserTurn(**defaults)


class _FakeSchemaPackCache:
    def __init__(self, index: dict[tuple[str, str], tuple[str, ...]]) -> None:
        self._index = index

    def current(self) -> dict[tuple[str, str], tuple[str, ...]]:
        return self._index


class _FakeDiscovery:
    def __init__(self, result: DiscoveryResult) -> None:
        self._result = result
        self.calls: list[tuple] = []

    async def discover(self, query, ask_indices):
        self.calls.append((query, ask_indices))
        return self._result


async def test_a_single_ask_resolves_and_returns_the_discovery_result() -> None:
    ask = Ask(
        subject_categories=SubjectCategory.MARKET,
        interaction_type=InteractionType.OBSERVE,
    )
    intent = Intent(asks=(ask,), confidence=0.9)
    turn = _turn()
    schema_pack_cache = _FakeSchemaPackCache(
        {("Market", "Service"): ("openagrinet:MandiPrice",)}
    )
    expected_result = DiscoveryResult(
        answers={0: ()},
        capabilities={
            0: (
                ProviderCapability("mausamgram", "IMD", "openagrinet:MandiPrice", "r1"),
            )
        },
        failures={0: ()},
        events=(),
    )
    discovery = _FakeDiscovery(expected_result)

    result = await discover_providers(
        intent, turn, discovery, schema_pack_cache, radius_m=25000
    )

    assert result == expected_result
    assert len(discovery.calls) == 1
    query, ask_indices = discovery.calls[0]
    assert query.capabilities == ("openagrinet:MandiPrice",)
    assert ask_indices == (0,)


async def test_an_unresolved_ask_never_calls_discover() -> None:
    ask = Ask(
        subject_categories=SubjectCategory.SCHEME,
        interaction_type=InteractionType.ACT,
    )
    intent = Intent(asks=(ask,), confidence=0.7)
    turn = _turn()
    schema_pack_cache = _FakeSchemaPackCache({})
    discovery = _FakeDiscovery(
        DiscoveryResult(answers={}, capabilities={}, failures={}, events=())
    )

    result = await discover_providers(
        intent, turn, discovery, schema_pack_cache, radius_m=25000
    )

    assert discovery.calls == []
    assert result.answers == {0: ()}
    assert result.capabilities == {0: ()}
    assert result.failures == {0: ()}
    assert result.events == (CapabilityUnresolved("Scheme", "Service"),)


async def test_two_asks_sharing_a_pair_dedupe_to_one_query() -> None:
    ask = Ask(
        subject_categories=SubjectCategory.MARKET,
        interaction_type=InteractionType.OBSERVE,
    )
    intent = Intent(asks=(ask, ask), confidence=0.8)
    turn = _turn()
    schema_pack_cache = _FakeSchemaPackCache(
        {("Market", "Service"): ("openagrinet:MandiPrice",)}
    )
    discovery = _FakeDiscovery(
        DiscoveryResult(
            answers={0: (), 1: ()},
            capabilities={0: (), 1: ()},
            failures={0: (), 1: ()},
            events=(),
        )
    )

    await discover_providers(intent, turn, discovery, schema_pack_cache, radius_m=25000)

    assert len(discovery.calls) == 1
    _, ask_indices = discovery.calls[0]
    assert set(ask_indices) == {0, 1}
