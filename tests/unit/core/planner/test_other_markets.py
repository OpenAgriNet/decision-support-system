from __future__ import annotations

from dss.core.planner.other_markets import (
    commodity_codes_in,
    find_other_markets,
    market_name,
)
from dss.core.provider_discovery.models import ProviderCapability
from dss.core.shared.models import Geometry
from tests.support.providers import build_capability

ONION = "23"
TOMATO = "65"
RAHURI = [74.66, 19.64]


def _market(
    code: str,
    *,
    district: str,
    at: list[float] | None = None,
    trades: tuple[str, ...] = (ONION,),
) -> ProviderCapability:
    market: dict = {
        "marketCode": code,
        "marketName": f"{code} APMC",
        "district": district,
    }
    if at is not None:
        market["location"] = {"geo": {"type": "Point", "coordinates": at}}
    return build_capability(
        provider_id="agmarknet",
        provider_name="Agmarknet Vistaar",
        capability="openagrinet:MandiPrice",
        resource_id=f"resource:mandi-price:market:{code}",
        observed_categories=("Market",),
        advertised={
            "market": market,
            "supportedCommodities": [{"code": c, "name": c} for c in trades],
        },
    )


def _ids(markets: tuple[ProviderCapability, ...]) -> list[str]:
    return [one.resource_id.rsplit(":", 1)[-1] for one in markets]


def test_markets_in_other_districts_come_nearest_first() -> None:
    asked = _market("rahuri", district="338", at=RAHURI)
    far = _market("pune", district="340", at=[73.85, 18.52])
    near = _market("nashik", district="339", at=[73.79, 20.00])

    others = find_other_markets(
        (asked, far, near),
        tried=(asked,),
        near=Geometry(coordinates=RAHURI),
        commodity_codes=frozenset({ONION}),
    )

    assert _ids(others) == ["nashik", "pune"]


def test_a_market_in_a_tried_district_is_skipped() -> None:
    """Agmarknet prices a market's whole district at once, so an empty answer
    for one market is empty for its neighbours too."""

    asked = _market("rahuri", district="338", at=RAHURI)
    same_district = _market("sangamner", district="338", at=[74.2, 19.57])

    others = find_other_markets(
        (asked, same_district),
        tried=(asked,),
        near=Geometry(coordinates=RAHURI),
        commodity_codes=frozenset({ONION}),
    )

    assert others == ()


def test_one_market_per_district_the_nearest() -> None:
    asked = _market("rahuri", district="338", at=RAHURI)
    nashik = _market("nashik", district="339", at=[73.79, 20.00])
    lasalgaon = _market("lasalgaon", district="339", at=[74.23, 20.15])

    others = find_other_markets(
        (asked, nashik, lasalgaon),
        tried=(asked,),
        near=Geometry(coordinates=RAHURI),
        commodity_codes=frozenset({ONION}),
    )

    assert _ids(others) == ["lasalgaon"]


def test_a_market_that_does_not_trade_the_commodity_is_skipped() -> None:
    asked = _market("rahuri", district="338", at=RAHURI)
    tomato_only = _market("nashik", district="339", trades=(TOMATO,))

    others = find_other_markets(
        (asked, tomato_only),
        tried=(asked,),
        near=None,
        commodity_codes=frozenset({ONION}),
    )

    assert others == ()


def test_with_no_commodity_named_every_market_qualifies() -> None:
    asked = _market("rahuri", district="338")
    tomato_only = _market("nashik", district="339", trades=(TOMATO,))

    others = find_other_markets(
        (asked, tomato_only),
        tried=(asked,),
        near=None,
        commodity_codes=frozenset(),
    )

    assert _ids(others) == ["nashik"]


def test_a_market_with_no_coordinates_comes_last() -> None:
    asked = _market("rahuri", district="338", at=RAHURI)
    unplaced = _market("unplaced", district="341")
    placed = _market("pune", district="340", at=[73.85, 18.52])

    others = find_other_markets(
        (asked, unplaced, placed),
        tried=(asked,),
        near=Geometry(coordinates=RAHURI),
        commodity_codes=frozenset({ONION}),
    )

    assert _ids(others) == ["pune", "unplaced"]


def test_without_a_place_discovery_order_stands() -> None:
    asked = _market("rahuri", district="338", at=RAHURI)
    first = _market("pune", district="340", at=[73.85, 18.52])
    second = _market("nashik", district="339", at=[73.79, 20.00])

    others = find_other_markets(
        (asked, first, second),
        tried=(asked,),
        near=None,
        commodity_codes=frozenset({ONION}),
    )

    assert _ids(others) == ["pune", "nashik"]


def test_only_mandi_prices_have_other_markets() -> None:
    weather = build_capability()
    other_weather = build_capability(resource_id="res:mausamgram:other")

    others = find_other_markets(
        (weather, other_weather),
        tried=(weather,),
        near=None,
        commodity_codes=frozenset(),
    )

    assert others == ()


def test_a_market_is_named_as_it_advertised_itself() -> None:
    assert market_name(_market("nashik", district="339")) == "nashik APMC"


def test_a_market_with_no_name_falls_back_to_its_resource() -> None:
    nameless = build_capability(resource_id="resource:mandi-price:market:1")

    assert market_name(nameless) == "resource:mandi-price:market:1"


def test_the_commodity_codes_are_read_from_the_model_s_fields() -> None:
    model_filled = {"supportedCommodities": [{"code": ONION}, {"code": TOMATO}]}

    assert commodity_codes_in(model_filled) == frozenset({ONION, TOMATO})


def test_no_commodity_named_is_no_codes() -> None:
    assert commodity_codes_in({"market": {"marketCode": "1634"}}) == frozenset()


def test_a_market_is_no_alternative_to_another_kind_of_data() -> None:
    """The model wrote its fields for the @type it picked; a market was never
    what it asked."""

    weather = build_capability()
    market = _market("nashik", district="339")

    others = find_other_markets(
        (weather, market),
        tried=(weather,),
        near=None,
        commodity_codes=frozenset(),
    )

    assert others == ()
