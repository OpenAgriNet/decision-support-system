from __future__ import annotations

from datetime import UTC, datetime, timedelta

from dss.core.provider_discovery.models import (
    AsksToBeDiscovered,
    AskUnservable,
    DiscoveredAnswer,
    DiscoveredAsk,
    DiscoveredAsks,
    ProviderQuery,
    ProviderQueryResult,
    Validity,
)

NOW = datetime(2026, 8, 26, 12, 0, 0, tzinfo=UTC)
_NOTHING_FOUND = ProviderQueryResult(answers=(), capabilities=(), failures=())

_MARKET_QUERY = ProviderQuery(
    capabilities=("openagrinet:MandiPrice",),
    subject_category="Market",
    languages=("hi",),
    coverage=None,
)
_WEATHER_QUERY = ProviderQuery(
    capabilities=("openagrinet:WeatherObservation",),
    subject_category="Weather",
    languages=("hi",),
    coverage=None,
)
_MARKET_WEATHER_MARKET = AsksToBeDiscovered(
    queries_by_ask={0: _MARKET_QUERY, 1: _WEATHER_QUERY, 2: _MARKET_QUERY},
    unsearchable_asks=frozenset(),
    events=(),
)


def test_each_shared_query_is_sent_once_in_first_seen_order() -> None:
    assert _MARKET_WEATHER_MARKET.find_unique_queries() == (
        _MARKET_QUERY,
        _WEATHER_QUERY,
    )


def test_the_asks_sharing_a_query_come_in_ask_order() -> None:
    assert _MARKET_WEATHER_MARKET.find_asks_sharing(_MARKET_QUERY) == (0, 2)
    assert _MARKET_WEATHER_MARKET.find_asks_sharing(_WEATHER_QUERY) == (1,)


def _answer(validity: Validity | None) -> DiscoveredAnswer:
    return DiscoveredAnswer(
        provider_id="agmarknet",
        provider_name="AGMARKNET",
        capability="openagrinet:MandiPrice",
        resource_id="r1",
        attributes={},
        validity=validity,
    )


def test_a_result_has_an_entry_per_ask_in_ask_order() -> None:
    answer = _answer(None)
    discovered = DiscoveredAsks(
        asks=(
            DiscoveredAsk(
                index=1,
                query=_MARKET_QUERY,
                query_result=ProviderQueryResult(
                    answers=(answer,), capabilities=(), failures=()
                ),
            ),
        ),
        unsearchable_asks=frozenset({0}),
    )

    result = discovered.build_result(())

    assert list(result.answers) == [0, 1]
    assert result.answers == {0: (), 1: (answer,)}
    assert result.failures == {0: (), 1: ()}


def test_a_searched_ask_with_nothing_found_is_unservable() -> None:
    searched = DiscoveredAsk(index=0, query=_MARKET_QUERY, query_result=_NOTHING_FOUND)

    assert searched.build_outcome_events() == (
        AskUnservable(0, ("openagrinet:MandiPrice",)),
    )


def test_within_validity_returns_a_new_ask_without_the_answers_outside_it() -> None:
    stale = _answer(
        Validity(starts_at=NOW - timedelta(days=2), ends_at=NOW - timedelta(days=1))
    )
    ask = DiscoveredAsk(
        index=0,
        query=_MARKET_QUERY,
        query_result=ProviderQueryResult(
            answers=(stale,), capabilities=(), failures=()
        ),
    )

    assert ask.within_validity(NOW).query_result.answers == ()
    assert ask.query_result.answers == (stale,)
