"""Tier 2 — the chain of place sources: order, fall-through, failure."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from dss.adapters.area_lookup.chain import ChainedAreaLookup
from dss.adapters.observability.metrics import configure_metrics, reset_metrics
from dss.core.shared.models import Geometry
from dss.ports.area_lookup import AreaLookupUnavailable, AreaMatch
from tests.support.fakes import FakeAreaLookup

_PUNE = AreaMatch(
    name="Pune",
    region="IN-MH",
    within=("India", "Maharashtra"),
    geometry=Geometry(coordinates=[73.85, 18.52]),
)


@pytest.fixture
def reader() -> Iterator[InMemoryMetricReader]:
    reader = InMemoryMetricReader()
    configure_metrics(
        model_profile="test-profile",
        meter_provider=MeterProvider(metric_readers=[reader]),
    )
    yield reader
    reset_metrics()


def _counted(reader: InMemoryMetricReader) -> dict[tuple[str, str], int]:
    """The lookup counter as {(source, outcome): count}."""

    data = reader.get_metrics_data()
    if data is None:
        return {}
    return {
        (point.attributes["source"], point.attributes["outcome"]): point.value
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == "dss.area_lookup.count"
        for point in metric.data.data_points
    }


class _Down:
    async def resolve(self, name: str, region: str | None = None) -> list[AreaMatch]:
        raise AreaLookupUnavailable("down")


def test_two_sources_with_one_name_refuse_to_start() -> None:
    """A name is the dashboard's label for a source. Two sources sharing one
    would mix their counts, so the chain refuses at startup."""

    with pytest.raises(ValueError, match="csv"):
        ChainedAreaLookup([("csv", FakeAreaLookup()), ("csv", FakeAreaLookup())])


def test_a_source_without_a_name_refuses_to_start() -> None:
    """An empty label would show on the dashboard as a line with no name."""

    with pytest.raises(ValueError, match="name"):
        ChainedAreaLookup([(" ", FakeAreaLookup())])


async def test_first_hit_stops_the_chain() -> None:
    """A source that answers ends the search: the next one is never asked."""

    first = FakeAreaLookup({"pune": [_PUNE]})
    second = FakeAreaLookup({"pune": [_PUNE]})
    chain = ChainedAreaLookup([("csv", first), ("photon", second)])

    assert await chain.resolve("Pune") == [_PUNE]
    assert second.calls == []


async def test_the_source_that_answered_is_counted(reader) -> None:
    """The dashboard shows how far down the chain a name had to go. A source
    that was never asked leaves no point. The count is what the source did,
    not what the farmer is asked: core decides that."""

    first = FakeAreaLookup({"pune": [_PUNE]})
    chain = ChainedAreaLookup([("csv", first), ("photon", FakeAreaLookup())])

    await chain.resolve("Pune")

    assert _counted(reader) == {("csv", "hit"): 1}


async def test_a_source_that_knows_nothing_is_counted_as_a_miss(reader) -> None:
    """Villages the file lacks are the reason Photon exists. The dashboard
    shows how often the file misses and Photon has to answer."""

    second = FakeAreaLookup({"pune": [_PUNE]})
    chain = ChainedAreaLookup([("csv", FakeAreaLookup()), ("photon", second)])

    await chain.resolve("Pune")

    assert _counted(reader) == {("csv", "miss"): 1, ("photon", "hit"): 1}


async def test_a_guess_lets_the_next_source_try_for_the_exact_name() -> None:
    """The file only has "Kanha Chatti", far away. Photon may know the real
    Kanha, so a guess must not stop the chain."""

    guess = _PUNE.model_copy(update={"name": "Kanha Chatti", "is_guess": True})
    exact = _PUNE.model_copy(update={"name": "Kanha"})
    chain = ChainedAreaLookup(
        [
            ("csv", FakeAreaLookup({"kanha": [guess]})),
            ("photon", FakeAreaLookup({"kanha": [exact]})),
        ]
    )

    assert await chain.resolve("Kanha") == [exact]


async def test_a_guess_is_kept_when_no_source_has_the_exact_name() -> None:
    """Nobody knows an exact Kanha. The guess comes back, still marked, so
    core can check it against the user's place or ask."""

    guess = _PUNE.model_copy(update={"name": "Kanha Chatti", "is_guess": True})
    chain = ChainedAreaLookup(
        [
            ("csv", FakeAreaLookup({"kanha": [guess]})),
            ("photon", FakeAreaLookup()),
        ]
    )

    assert await chain.resolve("Kanha") == [guess]


async def test_nearest_comes_from_the_first_source_that_knows() -> None:
    """A source with nothing near the point hands over to the next, the same
    order as a name lookup."""

    chain = ChainedAreaLookup(
        [("csv", FakeAreaLookup()), ("photon", FakeAreaLookup(nearest=_PUNE))]
    )

    assert await chain.nearest(Geometry(coordinates=[73.86, 18.53]), 50) == _PUNE


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


async def test_a_failed_source_is_counted_as_an_error(reader) -> None:
    """A source that is down must show on the dashboard. Without this the
    turn still answers, and nobody sees that Photon has been failing."""

    second = FakeAreaLookup({"pune": [_PUNE]})
    chain = ChainedAreaLookup([("photon", _Down()), ("other", second)])

    await chain.resolve("Pune")

    assert _counted(reader) == {("photon", "error"): 1, ("other", "hit"): 1}


async def test_a_failed_source_falls_to_the_next() -> None:
    """A source that is down counts as empty. The farmer's turn goes on."""

    second = FakeAreaLookup({"pune": [_PUNE]})
    chain = ChainedAreaLookup([("photon", _Down()), ("other", second)])

    assert await chain.resolve("Pune") == [_PUNE]
