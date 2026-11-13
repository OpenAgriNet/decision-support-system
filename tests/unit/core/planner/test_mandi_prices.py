from __future__ import annotations

from dss.core.planner.mandi_prices import (
    market_name,
    pick_market_prices,
    render_market_prices,
)
from dss.core.provider_discovery.models import DiscoveredAnswer, ProviderCapability
from tests.support.providers import build_capability

MANDI = "openagrinet:MandiPrice"


def _asked(name: str = "Rahuri APMC") -> ProviderCapability:
    return build_capability(
        provider_id="agmarknet",
        provider_name="Agmarknet Vistaar",
        capability=MANDI,
        resource_id="resource:mandi-price:market:1634",
        advertised={"market": {"marketCode": "1634", "marketName": name}},
    )


def _price(market: str, day: str, variety: str = "Local") -> DiscoveredAnswer:
    return DiscoveredAnswer(
        provider_id="agmarknet",
        provider_name="Agmarknet Vistaar",
        capability=MANDI,
        resource_id=f"res:{market}:{day}:{variety}",
        attributes={
            "market": {"marketName": market, "district": "Ahilyanagar"},
            "arrivalDate": day,
            "variety": variety,
            "prices": {"modal": 2800},
        },
        validity=None,
    )


def _ids(answers: tuple[DiscoveredAnswer, ...]) -> list[str]:
    return [answer.resource_id for answer in answers]


def test_the_asked_market_s_prices_answer_the_ask() -> None:
    """Agmarknet sends the whole district; the farmer asked about one market.
    Its name is written differently in the catalog and in the prices."""

    prices = pick_market_prices(
        (
            _price("APMC Sangamner ", "2026-11-05"),
            _price("APMC Rahuri ", "2026-11-05"),
        ),
        asked=_asked(),
    )

    assert prices.is_from_asked_market
    assert _ids(prices.answers) == ["res:APMC Rahuri :2026-11-05:Local"]


def test_without_the_asked_market_every_other_market_answers() -> None:
    prices = pick_market_prices(
        (
            _price("APMC Sangamner ", "2026-11-05"),
            _price("APMC Kopargaon ", "2026-11-04"),
        ),
        asked=_asked(),
    )

    assert not prices.is_from_asked_market
    assert _ids(prices.answers) == [
        "res:APMC Sangamner :2026-11-05:Local",
        "res:APMC Kopargaon :2026-11-04:Local",
    ]


def test_each_market_keeps_only_its_latest_day() -> None:
    """A week of prices for one market is one price and its date to a farmer,
    not seven to average."""

    prices = pick_market_prices(
        (
            _price("APMC Rahuri ", "2026-11-03"),
            _price("APMC Rahuri ", "2026-11-05", variety="Red"),
            _price("APMC Rahuri ", "2026-11-05", variety="Other"),
            _price("APMC Rahuri ", "2026-11-04"),
        ),
        asked=_asked(),
    )

    assert _ids(prices.answers) == [
        "res:APMC Rahuri :2026-11-05:Red",
        "res:APMC Rahuri :2026-11-05:Other",
    ]


def test_other_markets_each_keep_their_own_latest_day() -> None:
    prices = pick_market_prices(
        (
            _price("APMC Sangamner ", "2026-11-04"),
            _price("APMC Sangamner ", "2026-11-05"),
            _price("APMC Kopargaon ", "2026-11-01"),
        ),
        asked=_asked(),
    )

    assert _ids(prices.answers) == [
        "res:APMC Sangamner :2026-11-05:Local",
        "res:APMC Kopargaon :2026-11-01:Local",
    ]


def test_a_market_whose_name_only_contains_the_asked_one_is_another_market() -> None:
    prices = pick_market_prices(
        (_price("Newasa(Ghodegaon) ", "2026-11-05"),),
        asked=_asked("Newasa APMC"),
    )

    assert not prices.is_from_asked_market


def test_other_data_is_passed_through_untouched() -> None:
    weather = build_capability()
    forecast = DiscoveredAnswer(
        provider_id="mausamgram",
        provider_name="IMD",
        capability="openagrinet:WeatherObservation",
        resource_id="res:forecast",
        attributes={"temperature": 31},
        validity=None,
    )

    prices = pick_market_prices((forecast,), asked=weather)

    assert prices.is_from_asked_market
    assert prices.answers == (forecast,)


def test_a_market_is_named_as_it_advertised_itself() -> None:
    assert market_name(_asked()) == "Rahuri APMC"


def test_a_market_with_no_name_falls_back_to_its_resource() -> None:
    nameless = build_capability(resource_id="resource:mandi-price:market:1")

    assert market_name(nameless) == "resource:mandi-price:market:1"


def test_the_asked_market_s_prices_are_shown_as_they_are() -> None:
    prices = pick_market_prices((_price("APMC Rahuri ", "2026-11-05"),), asked=_asked())

    shown = render_market_prices(prices, asked=_asked())

    assert "has no price" not in shown
    assert "APMC Rahuri" in shown


def test_other_markets_prices_say_the_asked_market_had_none() -> None:
    """Without this the model reads them as the wrong place's data."""

    prices = pick_market_prices(
        (_price("APMC Sangamner ", "2026-11-05"),), asked=_asked()
    )

    shown = render_market_prices(prices, asked=_asked())

    assert shown.startswith("Rahuri APMC has no price in this window.")
    assert "APMC Sangamner" in shown
