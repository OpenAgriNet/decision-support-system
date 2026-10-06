"""Tier 2 — the chain of place sources: order, fall-through, failure."""

from __future__ import annotations

from dss.adapters.area_lookup.chain import ChainedAreaLookup
from dss.core.shared.models import Geometry
from dss.ports.area_lookup import AreaLookupUnavailable, AreaMatch
from tests.support.fakes import FakeAreaLookup

_PUNE = AreaMatch(
    name="Pune",
    region="IN-MH",
    within=("India", "Maharashtra"),
    geometry=Geometry(coordinates=[73.85, 18.52]),
)


class _Down:
    async def resolve(self, name: str, region: str | None = None) -> list[AreaMatch]:
        raise AreaLookupUnavailable("down")


async def test_first_hit_stops_the_chain() -> None:
    """A source that answers ends the search: the next one is never asked."""

    first = FakeAreaLookup({"pune": [_PUNE]})
    second = FakeAreaLookup({"pune": [_PUNE]})
    chain = ChainedAreaLookup([("csv", first), ("photon", second)])

    assert await chain.resolve("Pune") == [_PUNE]
    assert second.calls == []


async def test_a_miss_falls_to_the_next_source() -> None:
    """A source that does not know the name hands over to the next one."""

    first = FakeAreaLookup()
    second = FakeAreaLookup({"pune": [_PUNE]})
    chain = ChainedAreaLookup([("csv", first), ("photon", second)])

    assert await chain.resolve("Pune") == [_PUNE]


async def test_all_sources_empty_is_empty() -> None:
    """No source knows the name: the farmer's place is reported as not found."""

    chain = ChainedAreaLookup([("csv", FakeAreaLookup()), ("photon", FakeAreaLookup())])

    assert await chain.resolve("Pune") == []


async def test_a_failed_source_falls_to_the_next() -> None:
    """A source that is down counts as empty. The farmer's turn goes on."""

    second = FakeAreaLookup({"pune": [_PUNE]})
    chain = ChainedAreaLookup([("photon", _Down()), ("other", second)])

    assert await chain.resolve("Pune") == [_PUNE]
