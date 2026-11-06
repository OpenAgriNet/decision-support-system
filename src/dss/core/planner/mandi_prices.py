"""Which of a MandiPrice select's prices answer the ask.

Agmarknet answers for the asked market's whole district, over the whole
validity window, so one select brings back many markets and several days. The
asked market's own prices answer the ask when it has any; otherwise the other
markets' prices do. Each market keeps only its latest day, so the farmer hears
one price per market, with its date, rather than a week of them to average.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from dss.core.provider_discovery.models import DiscoveredAnswer, ProviderCapability

_MANDI_PRICE = "openagrinet:MandiPrice"
# Words that say "a market" rather than which one. The catalog writes
# "Rahuri APMC" where the prices write "APMC Rahuri ".
_GENERIC_MARKET_WORDS = frozenset({"apmc", "market", "mandi"})


@dataclass(frozen=True)
class MarketPrices:
    answers: tuple[DiscoveredAnswer, ...]
    is_from_asked_market: bool


def pick_market_prices(
    answers: Iterable[DiscoveredAnswer], *, asked: ProviderCapability
) -> MarketPrices:
    """The asked market's latest prices, or, when it has none, every other
    market's latest. Anything but MandiPrice passes through untouched."""

    answers = tuple(answers)
    if asked.capability != _MANDI_PRICE:
        return MarketPrices(answers=answers, is_from_asked_market=True)

    asked_key = _name_key(market_name(asked))
    own = tuple(
        answer for answer in answers if _name_key(_market_of(answer)) == asked_key
    )
    return MarketPrices(
        answers=_latest_day_per_market(own or answers),
        is_from_asked_market=bool(own),
    )


def market_name(capability: ProviderCapability) -> str:
    market = capability.advertised.get("market")
    name = market.get("marketName") if isinstance(market, dict) else None
    return name or capability.resource_id


def _market_of(answer: DiscoveredAnswer) -> str:
    market = answer.attributes.get("market")
    return (market.get("marketName") if isinstance(market, dict) else None) or ""


def _name_key(name: str) -> frozenset[str]:
    words = re.findall(r"[a-z0-9]+", name.lower())
    return frozenset(words) - _GENERIC_MARKET_WORDS


def _arrival_date_of(answer: DiscoveredAnswer) -> str:
    # ISO dates, so the latest is the largest string.
    return str(answer.attributes.get("arrivalDate") or "")


def _latest_day_per_market(
    answers: tuple[DiscoveredAnswer, ...],
) -> tuple[DiscoveredAnswer, ...]:
    latest: dict[frozenset[str], str] = {}
    for answer in answers:
        market = _name_key(_market_of(answer))
        latest[market] = max(latest.get(market, ""), _arrival_date_of(answer))
    return tuple(
        answer
        for answer in answers
        if _arrival_date_of(answer) == latest[_name_key(_market_of(answer))]
    )
