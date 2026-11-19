from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from dss.core.provider_discovery.models import (
    NOTHING_FOUND,
    AnswerSource,
    AsksToBeDiscovered,
    AskToDiscover,
    AskUnservable,
    DiscoveredAnswer,
    DiscoveredAsk,
    DiscoveryResult,
    ProviderQuery,
    ProviderQueryResult,
    Validity,
)
from tests.support.asks import an_ask

NOW = datetime(2026, 8, 26, 12, 0, 0, tzinfo=UTC)

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
    asks=(
        AskToDiscover(an_ask(id=0), _MARKET_QUERY),
        AskToDiscover(an_ask(id=1), _WEATHER_QUERY),
        AskToDiscover(an_ask(id=2), _MARKET_QUERY),
    ),
    events=(),
)


def test_each_shared_query_is_sent_once_in_first_seen_order() -> None:
    assert _MARKET_WEATHER_MARKET.find_unique_queries() == (
        _MARKET_QUERY,
        _WEATHER_QUERY,
    )


def _answer(validity: Validity | None) -> DiscoveredAnswer:
    return DiscoveredAnswer(
        provider_id="agmarknet",
        provider_name="AGMARKNET",
        capability="openagrinet:MandiPrice",
        resource_id="r1",
        attributes={},
        validity=validity,
    )


def test_a_searched_ask_with_nothing_found_is_unservable() -> None:
    searched = DiscoveredAsk(ask=an_ask(), query=_MARKET_QUERY, found=NOTHING_FOUND)

    assert searched.build_outcome_events() == (
        AskUnservable(0, ("openagrinet:MandiPrice",)),
    )


def test_an_ask_never_searched_is_not_called_unservable() -> None:
    """Nobody was asked, so "nobody serves it" would be a false report."""

    never_searched = DiscoveredAsk(ask=an_ask(), query=None, found=NOTHING_FOUND)

    assert never_searched.build_outcome_events() == ()


def test_two_entries_for_one_ask_are_refused() -> None:
    """A reader looking an ask up by id would silently get only the first."""

    twice = DiscoveredAsk(ask=an_ask(id=1), query=None, found=NOTHING_FOUND)

    with pytest.raises(ValueError, match="two entries for ask 1"):
        DiscoveryResult(asks=(twice, twice), events=())


def test_within_validity_returns_a_new_ask_without_the_answers_outside_it() -> None:
    stale = _answer(
        Validity(starts_at=NOW - timedelta(days=2), ends_at=NOW - timedelta(days=1))
    )
    ask = DiscoveredAsk(
        ask=an_ask(),
        query=_MARKET_QUERY,
        found=ProviderQueryResult(answers=(stale,), capabilities=(), failures=()),
    )

    assert ask.within_validity(NOW).answers == ()
    assert ask.answers == (stale,)


def test_an_answer_names_its_origin() -> None:
    relayed = replace(_answer(None), source_id="imd")

    assert relayed.origin == AnswerSource(provider_id="agmarknet", source_id="imd")
