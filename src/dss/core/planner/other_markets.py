"""Other markets to price an ask at, when the market asked about had none.

Agmarknet answers a MandiPrice select for the market's whole district, so an
empty answer means the district had no price in the window. The next market
worth asking is in another district — nearest first, one per district.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

from dss.core.provider_discovery.models import ProviderCapability
from dss.core.shared.geo import distance_km
from dss.core.shared.models import Geometry

_MANDI_PRICE = "openagrinet:MandiPrice"


def find_other_markets(
    candidates: Iterable[ProviderCapability],
    *,
    tried: Iterable[ProviderCapability],
    near: Geometry | None,
    commodity_codes: frozenset[str],
) -> tuple[ProviderCapability, ...]:
    """The nearest MandiPrice market of each district not yet tried that
    trades one of `commodity_codes`, nearest first. With no codes, any market
    qualifies; with no place, discovery's order stands. None unless what was
    tried is MandiPrice: the model wrote its fields for that @type alone."""

    tried = tuple(tried)
    if not tried or any(market.capability != _MANDI_PRICE for market in tried):
        return ()
    tried_districts = {_district_of(market) for market in tried}
    tried_ids = {market.resource_id for market in tried}
    eligible = [
        market
        for market in candidates
        if market.capability == _MANDI_PRICE
        and market.resource_id not in tried_ids
        and _district_of(market) not in tried_districts
        and _trades_any(market, commodity_codes)
    ]
    nearest_first = sorted(eligible, key=lambda market: _distance_km(market, near))
    return tuple(_nearest_per_district(nearest_first))


def commodity_codes_in(model_filled: Mapping[str, object]) -> frozenset[str]:
    commodities = model_filled.get("supportedCommodities")
    if not isinstance(commodities, list):
        return frozenset()
    return frozenset(
        item["code"]
        for item in commodities
        if isinstance(item, dict) and isinstance(item.get("code"), str)
    )


def market_name(capability: ProviderCapability) -> str:
    return _market(capability).get("marketName") or capability.resource_id


def _market(capability: ProviderCapability) -> dict:
    market = capability.advertised.get("market")
    return market if isinstance(market, dict) else {}


def _district_of(capability: ProviderCapability) -> str:
    # A market with no district is a district of its own: nothing says an
    # empty answer elsewhere covers it.
    return _market(capability).get("district") or capability.resource_id


def _trades_any(
    capability: ProviderCapability, commodity_codes: frozenset[str]
) -> bool:
    if not commodity_codes:
        return True
    traded = capability.advertised.get("supportedCommodities") or ()
    return any(
        isinstance(item, dict) and item.get("code") in commodity_codes
        for item in traded
    )


def _distance_km(capability: ProviderCapability, near: Geometry | None) -> float:
    point = _point_of(capability)
    if near is None or point is None:
        return math.inf
    return distance_km(near.coordinates, point)


def _point_of(capability: ProviderCapability) -> list[float] | None:
    geo = (_market(capability).get("location") or {}).get("geo") or {}
    if geo.get("type") != "Point":
        return None
    return geo.get("coordinates")


def _nearest_per_district(
    nearest_first: list[ProviderCapability],
) -> Iterable[ProviderCapability]:
    seen: set[str] = set()
    for market in nearest_first:
        district = _district_of(market)
        if district not in seen:
            seen.add(district)
            yield market
