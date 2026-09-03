"""Tests for discover_providers — ties resolution, the schema-pack index, and
CapabilityDiscovery together.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from dss.core.intent.models import Ask, Intent, InteractionType, SubjectCategory
from dss.core.provider_discovery.models import (
    AskDiscoveryFailed,
    AskUnservable,
    CapabilityUnresolved,
    CategoryMappingDiverged,
    DiscoveredAnswer,
    DiscoveryFailure,
    DiscoveryResult,
    ExpiredAnswerDropped,
    FailureClass,
    ProviderCapability,
    Validity,
)
from dss.core.provider_discovery.service import discover_providers
from dss.core.shared.models import UserTurn

NOW = datetime(2026, 8, 26, 12, 0, 0, tzinfo=UTC)


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
        intent, turn, discovery, schema_pack_cache, radius_m=25000, now=NOW
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
        intent, turn, discovery, schema_pack_cache, radius_m=25000, now=NOW
    )

    assert discovery.calls == []
    assert result.answers == {0: ()}
    assert result.capabilities == {0: ()}
    assert result.failures == {0: ()}
    assert result.events == (CapabilityUnresolved("Scheme", "Service"),)


class _PartiallyFailingDiscovery:
    """Returns a failure result for one capability, succeeds for another —
    proves one query's failure doesn't prevent a concurrent sibling from
    completing. discover() never raises — failures are data.
    """

    def __init__(self, fails_for: str) -> None:
        self._fails_for = fails_for
        self.calls: list[tuple] = []

    async def discover(self, query, ask_indices):
        self.calls.append((query, ask_indices))
        if self._fails_for in query.capabilities:
            return DiscoveryResult(
                answers={i: () for i in ask_indices},
                capabilities={i: () for i in ask_indices},
                failures={
                    i: (
                        DiscoveryFailure(
                            capability=self._fails_for,
                            status_code=500,
                            failure_class=FailureClass.TRANSIENT,
                        ),
                    )
                    for i in ask_indices
                },
                events=(),
            )
        return DiscoveryResult(
            answers={i: () for i in ask_indices},
            capabilities={
                i: (
                    ProviderCapability(
                        "mausamgram", "IMD", query.capabilities[0], "r1"
                    ),
                )
                for i in ask_indices
            },
            failures={i: () for i in ask_indices},
            events=(),
        )


async def test_a_failing_query_does_not_prevent_a_sibling_from_succeeding() -> None:
    weather_ask = Ask(
        subject_categories=SubjectCategory.WEATHER,
        interaction_type=InteractionType.OBSERVE,
    )
    market_ask = Ask(
        subject_categories=SubjectCategory.MARKET,
        interaction_type=InteractionType.OBSERVE,
    )
    intent = Intent(asks=(weather_ask, market_ask), confidence=0.8)
    turn = _turn()
    schema_pack_cache = _FakeSchemaPackCache(
        {
            ("Weather", "Service"): ("openagrinet:WeatherObservation",),
            ("Market", "Service"): ("openagrinet:MandiPrice",),
        }
    )
    discovery = _PartiallyFailingDiscovery(fails_for="openagrinet:WeatherObservation")

    result = await discover_providers(
        intent, turn, discovery, schema_pack_cache, radius_m=25000, now=NOW
    )

    assert len(discovery.calls) == 2
    assert result.capabilities[1][0].capability == "openagrinet:MandiPrice"
    assert result.failures[0] != ()
    assert result.answers[0] == ()
    assert result.capabilities[0] == ()
    assert result.events == (
        AskDiscoveryFailed(
            ask_index=0,
            capability="openagrinet:WeatherObservation",
            failure_class=FailureClass.TRANSIENT,
            status_code=500,
        ),
    )


class _RaisingDiscovery:
    """Violates the CapabilityDiscovery contract by raising. Stands in for a
    defective adapter, to pin down what that costs: anyio's task group
    cancels every sibling, so the whole turn dies. Adapters must return
    failures as data — see the mapping guard in adapters/discovery/client.py.
    """

    def __init__(self, raises_for: str) -> None:
        self._raises_for = raises_for
        self.calls: list[tuple] = []

    async def discover(self, query, ask_indices):
        self.calls.append((query, ask_indices))
        if self._raises_for in query.capabilities:
            raise KeyError("resources")
        return DiscoveryResult(
            answers={i: () for i in ask_indices},
            capabilities={i: () for i in ask_indices},
            failures={i: () for i in ask_indices},
            events=(),
        )


async def test_an_adapter_that_raises_takes_the_whole_turn_down() -> None:
    """Documents why the adapter must never let an exception escape: core
    gives it no safety net, by design — a raise here is a fail-fast bug
    signal, not a per-query failure mode.
    """
    intent = Intent(
        asks=(
            Ask(
                subject_categories=SubjectCategory.WEATHER,
                interaction_type=InteractionType.OBSERVE,
            ),
            Ask(
                subject_categories=SubjectCategory.MARKET,
                interaction_type=InteractionType.OBSERVE,
            ),
        ),
        confidence=0.8,
    )
    schema_pack_cache = _FakeSchemaPackCache(
        {
            ("Weather", "Service"): ("openagrinet:WeatherObservation",),
            ("Market", "Service"): ("openagrinet:MandiPrice",),
        }
    )
    discovery = _RaisingDiscovery(raises_for="openagrinet:WeatherObservation")

    with pytest.raises(BaseExceptionGroup):
        await discover_providers(
            intent, _turn(), discovery, schema_pack_cache, radius_m=25000, now=NOW
        )


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

    await discover_providers(
        intent, turn, discovery, schema_pack_cache, radius_m=25000, now=NOW
    )

    assert len(discovery.calls) == 1
    _, ask_indices = discovery.calls[0]
    assert set(ask_indices) == {0, 1}


def _expired_answer() -> DiscoveredAnswer:
    return DiscoveredAnswer(
        provider_id="agmarknet",
        provider_name="AGMARKNET",
        capability="openagrinet:MandiPrice",
        resource_id="r1",
        attributes={},
        validity=Validity(
            starts_at=NOW - timedelta(days=2),
            ends_at=NOW - timedelta(days=1),
        ),
    )


async def test_an_expired_answer_with_no_fallback_is_dropped() -> None:
    ask = Ask(
        subject_categories=SubjectCategory.MARKET,
        interaction_type=InteractionType.OBSERVE,
    )
    intent = Intent(asks=(ask,), confidence=0.9)
    turn = _turn()
    schema_pack_cache = _FakeSchemaPackCache(
        {("Market", "Service"): ("openagrinet:MandiPrice",)}
    )
    discovery = _FakeDiscovery(
        DiscoveryResult(
            answers={0: (_expired_answer(),)},
            capabilities={0: ()},
            failures={0: ()},
            events=(),
        )
    )

    result = await discover_providers(
        intent, turn, discovery, schema_pack_cache, radius_m=25000, now=NOW
    )

    assert result.answers[0] == ()
    assert result.events == (
        ExpiredAnswerDropped(
            provider_id="agmarknet",
            capability="openagrinet:MandiPrice",
            resource_id="r1",
            had_fallback=False,
        ),
        AskUnservable(ask_index=0, capabilities=("openagrinet:MandiPrice",)),
    )


async def test_an_expired_answer_with_an_on_demand_sibling_records_a_fallback() -> None:
    ask = Ask(
        subject_categories=SubjectCategory.MARKET,
        interaction_type=InteractionType.OBSERVE,
    )
    intent = Intent(asks=(ask,), confidence=0.9)
    turn = _turn()
    schema_pack_cache = _FakeSchemaPackCache(
        {("Market", "Service"): ("openagrinet:MandiPrice",)}
    )
    fallback_capability = ProviderCapability(
        "agmarknet", "AGMARKNET", "openagrinet:MandiPrice", "r2"
    )
    discovery = _FakeDiscovery(
        DiscoveryResult(
            answers={0: (_expired_answer(),)},
            capabilities={0: (fallback_capability,)},
            failures={0: ()},
            events=(),
        )
    )

    result = await discover_providers(
        intent, turn, discovery, schema_pack_cache, radius_m=25000, now=NOW
    )

    assert result.answers[0] == ()
    assert result.capabilities[0] == (fallback_capability,)
    assert result.events == (
        ExpiredAnswerDropped(
            provider_id="agmarknet",
            capability="openagrinet:MandiPrice",
            resource_id="r1",
            had_fallback=True,
        ),
    )


async def test_a_non_expired_answer_is_kept() -> None:
    ask = Ask(
        subject_categories=SubjectCategory.MARKET,
        interaction_type=InteractionType.OBSERVE,
    )
    intent = Intent(asks=(ask,), confidence=0.9)
    turn = _turn()
    schema_pack_cache = _FakeSchemaPackCache(
        {("Market", "Service"): ("openagrinet:MandiPrice",)}
    )
    fresh_answer = DiscoveredAnswer(
        provider_id="agmarknet",
        provider_name="AGMARKNET",
        capability="openagrinet:MandiPrice",
        resource_id="r1",
        attributes={},
        validity=Validity(
            starts_at=NOW - timedelta(hours=1), ends_at=NOW + timedelta(hours=1)
        ),
    )
    discovery = _FakeDiscovery(
        DiscoveryResult(
            answers={0: (fresh_answer,)},
            capabilities={0: ()},
            failures={0: ()},
            events=(),
        )
    )

    result = await discover_providers(
        intent, turn, discovery, schema_pack_cache, radius_m=25000, now=NOW
    )

    assert result.answers[0] == (fresh_answer,)
    assert result.events == ()


async def test_an_answer_with_no_validity_is_kept() -> None:
    ask = Ask(
        subject_categories=SubjectCategory.MARKET,
        interaction_type=InteractionType.OBSERVE,
    )
    intent = Intent(asks=(ask,), confidence=0.9)
    turn = _turn()
    schema_pack_cache = _FakeSchemaPackCache(
        {("Market", "Service"): ("openagrinet:MandiPrice",)}
    )
    answer_without_validity = DiscoveredAnswer(
        provider_id="agmarknet",
        provider_name="AGMARKNET",
        capability="openagrinet:MandiPrice",
        resource_id="r1",
        attributes={},
        validity=None,
    )
    discovery = _FakeDiscovery(
        DiscoveryResult(
            answers={0: (answer_without_validity,)},
            capabilities={0: ()},
            failures={0: ()},
            events=(),
        )
    )

    result = await discover_providers(
        intent, turn, discovery, schema_pack_cache, radius_m=25000, now=NOW
    )

    assert result.answers[0] == (answer_without_validity,)


async def test_a_capability_matching_the_index_has_no_divergence_event() -> None:
    ask = Ask(
        subject_categories=SubjectCategory.MARKET,
        interaction_type=InteractionType.OBSERVE,
    )
    intent = Intent(asks=(ask,), confidence=0.9)
    turn = _turn()
    schema_pack_cache = _FakeSchemaPackCache(
        {("Market", "Service"): ("openagrinet:MandiPrice",)}
    )
    matching_capability = ProviderCapability(
        "mausamgram",
        "IMD",
        "openagrinet:MandiPrice",
        "r1",
        observed_categories=("Market",),
    )
    discovery = _FakeDiscovery(
        DiscoveryResult(
            answers={0: ()},
            capabilities={0: (matching_capability,)},
            failures={0: ()},
            events=(),
        )
    )

    result = await discover_providers(
        intent, turn, discovery, schema_pack_cache, radius_m=25000, now=NOW
    )

    assert result.events == ()


async def test_a_capability_with_a_category_outside_the_index_diverges() -> None:
    ask = Ask(
        subject_categories=SubjectCategory.MARKET,
        interaction_type=InteractionType.OBSERVE,
    )
    intent = Intent(asks=(ask,), confidence=0.9)
    turn = _turn()
    schema_pack_cache = _FakeSchemaPackCache(
        {("Market", "Service"): ("openagrinet:MandiPrice",)}
    )
    diverging_capability = ProviderCapability(
        "mausamgram",
        "IMD",
        "openagrinet:MandiPrice",
        "r1",
        observed_categories=("Scheme",),
    )
    discovery = _FakeDiscovery(
        DiscoveryResult(
            answers={0: ()},
            capabilities={0: (diverging_capability,)},
            failures={0: ()},
            events=(),
        )
    )

    result = await discover_providers(
        intent, turn, discovery, schema_pack_cache, radius_m=25000, now=NOW
    )

    assert result.events == (
        CategoryMappingDiverged(
            capability="openagrinet:MandiPrice", observed_category="Scheme"
        ),
    )


async def test_a_direct_answer_with_a_diverging_category_is_flagged() -> None:
    ask = Ask(
        subject_categories=SubjectCategory.MARKET,
        interaction_type=InteractionType.OBSERVE,
    )
    intent = Intent(asks=(ask,), confidence=0.9)
    turn = _turn()
    schema_pack_cache = _FakeSchemaPackCache(
        {("Market", "Service"): ("openagrinet:MandiPrice",)}
    )
    diverging_answer = DiscoveredAnswer(
        provider_id="agmarknet",
        provider_name="AGMARKNET",
        capability="openagrinet:MandiPrice",
        resource_id="r1",
        attributes={"subjectCategories": ["Weather"]},
        validity=None,
    )
    discovery = _FakeDiscovery(
        DiscoveryResult(
            answers={0: (diverging_answer,)},
            capabilities={0: ()},
            failures={0: ()},
            events=(),
        )
    )

    result = await discover_providers(
        intent, turn, discovery, schema_pack_cache, radius_m=25000, now=NOW
    )

    assert result.events == (
        CategoryMappingDiverged(
            capability="openagrinet:MandiPrice", observed_category="Weather"
        ),
    )


async def test_an_empty_catalog_result_emits_ask_unservable() -> None:
    ask = Ask(
        subject_categories=SubjectCategory.MARKET,
        interaction_type=InteractionType.OBSERVE,
    )
    intent = Intent(asks=(ask,), confidence=0.9)
    turn = _turn()
    schema_pack_cache = _FakeSchemaPackCache(
        {("Market", "Service"): ("openagrinet:MandiPrice",)}
    )
    discovery = _FakeDiscovery(
        DiscoveryResult(
            answers={0: ()}, capabilities={0: ()}, failures={0: ()}, events=()
        )
    )

    result = await discover_providers(
        intent, turn, discovery, schema_pack_cache, radius_m=25000, now=NOW
    )

    assert result.events == (
        AskUnservable(ask_index=0, capabilities=("openagrinet:MandiPrice",)),
    )


async def test_a_failed_ask_emits_ask_discovery_failed_not_unservable() -> None:
    ask = Ask(
        subject_categories=SubjectCategory.MARKET,
        interaction_type=InteractionType.OBSERVE,
    )
    intent = Intent(asks=(ask,), confidence=0.9)
    turn = _turn()
    schema_pack_cache = _FakeSchemaPackCache(
        {("Market", "Service"): ("openagrinet:MandiPrice",)}
    )
    discovery = _FakeDiscovery(
        DiscoveryResult(
            answers={0: ()},
            capabilities={0: ()},
            failures={
                0: (
                    DiscoveryFailure(
                        capability="openagrinet:MandiPrice",
                        status_code=500,
                        failure_class=FailureClass.TRANSIENT,
                    ),
                )
            },
            events=(),
        )
    )

    result = await discover_providers(
        intent, turn, discovery, schema_pack_cache, radius_m=25000, now=NOW
    )

    assert result.events == (
        AskDiscoveryFailed(
            ask_index=0,
            capability="openagrinet:MandiPrice",
            failure_class=FailureClass.TRANSIENT,
            status_code=500,
        ),
    )


async def test_an_unresolved_ask_does_not_also_emit_ask_unservable() -> None:
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
        intent, turn, discovery, schema_pack_cache, radius_m=25000, now=NOW
    )

    assert result.events == (CapabilityUnresolved("Scheme", "Service"),)


async def test_a_resolved_answer_does_not_emit_ask_unservable() -> None:
    ask = Ask(
        subject_categories=SubjectCategory.MARKET,
        interaction_type=InteractionType.OBSERVE,
    )
    intent = Intent(asks=(ask,), confidence=0.9)
    turn = _turn()
    schema_pack_cache = _FakeSchemaPackCache(
        {("Market", "Service"): ("openagrinet:MandiPrice",)}
    )
    capability = ProviderCapability("mausamgram", "IMD", "openagrinet:MandiPrice", "r1")
    discovery = _FakeDiscovery(
        DiscoveryResult(
            answers={0: ()},
            capabilities={0: (capability,)},
            failures={0: ()},
            events=(),
        )
    )

    result = await discover_providers(
        intent, turn, discovery, schema_pack_cache, radius_m=25000, now=NOW
    )

    assert result.events == ()
