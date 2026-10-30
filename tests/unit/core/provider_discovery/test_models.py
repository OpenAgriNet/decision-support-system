from __future__ import annotations

from dss.core.provider_discovery.models import AsksToBeDiscovered, ProviderQuery

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
