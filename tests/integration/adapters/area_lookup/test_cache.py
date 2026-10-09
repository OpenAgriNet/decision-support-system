"""Tier 2 — the cache in front of a slow place source."""

from __future__ import annotations

import anyio
import pytest

from dss.adapters.area_lookup.cache import CachedAreaLookup
from dss.core.shared.models import Geometry
from dss.ports.area_lookup import AreaLookupUnavailable, AreaMatch
from tests.support.fakes import FakeAreaLookup

_ELDORET = AreaMatch(
    name="Eldoret",
    region="KE",
    within=("Kenya", "Uasin Gishu County", "Moiben"),
    geometry=Geometry(coordinates=[35.27, 0.52]),
)


async def test_a_repeat_question_skips_the_source() -> None:
    inner = FakeAreaLookup({"eldoret": [_ELDORET]})
    cache = CachedAreaLookup(inner, ttl_seconds=60, max_entries=100)

    first = await cache.resolve("Eldoret")
    second = await cache.resolve("Eldoret")

    assert first == second == [_ELDORET]
    assert len(inner.calls) == 1


class _DownOnce:
    """Fails on the first call, answers on the next."""

    def __init__(self) -> None:
        self.calls = 0

    async def resolve(self, name: str, region: str | None = None) -> list[AreaMatch]:
        self.calls += 1
        if self.calls == 1:
            raise AreaLookupUnavailable("down")
        return [_ELDORET]


async def test_an_error_is_not_remembered() -> None:
    """A source that is down is not the same as a name nobody knows. Caching
    the failure would hide a place for a whole day."""

    inner = _DownOnce()
    cache = CachedAreaLookup(inner, ttl_seconds=60, max_entries=100)

    with pytest.raises(AreaLookupUnavailable):
        await cache.resolve("Eldoret")

    assert await cache.resolve("Eldoret") == [_ELDORET]
    assert inner.calls == 2


async def test_an_old_answer_is_asked_again() -> None:
    """Places change names, and the source gets fixed. After the TTL the
    cache asks again."""

    inner = FakeAreaLookup({"eldoret": [_ELDORET]})
    cache = CachedAreaLookup(inner, ttl_seconds=0.05, max_entries=100)

    await cache.resolve("Eldoret")
    await anyio.sleep(0.1)
    await cache.resolve("Eldoret")

    assert len(inner.calls) == 2


async def test_the_oldest_answer_is_dropped_when_full() -> None:
    """Every distinct name is kept, so without a limit the cache only grows."""

    inner = FakeAreaLookup()
    cache = CachedAreaLookup(inner, ttl_seconds=60, max_entries=2)

    for name in ("a", "b", "c"):
        await cache.resolve(name)
    assert len(inner.calls) == 3

    await cache.resolve("b")
    await cache.resolve("c")
    assert len(inner.calls) == 3

    await cache.resolve("a")
    assert len(inner.calls) == 4


class _Slow:
    """Takes a moment to answer, so overlapping calls really overlap."""

    def __init__(self) -> None:
        self.calls = 0

    async def resolve(self, name: str, region: str | None = None) -> list[AreaMatch]:
        self.calls += 1
        await anyio.sleep(0.05)
        return [_ELDORET]


async def test_simultaneous_questions_share_one_call() -> None:
    """Two farmers ask about the same village at the same moment. One call to
    the source is enough for both."""

    inner = _Slow()
    cache = CachedAreaLookup(inner, ttl_seconds=60, max_entries=100)

    async with anyio.create_task_group() as tasks:
        for _ in range(3):
            tasks.start_soon(cache.resolve, "Eldoret")

    assert inner.calls == 1


async def test_a_miss_is_remembered_too() -> None:
    """A name nobody knows is asked about again and again. Each time is a
    slow call that finds nothing."""

    inner = FakeAreaLookup()
    cache = CachedAreaLookup(inner, ttl_seconds=60, max_entries=100)

    await cache.resolve("Zzqxvk")
    await cache.resolve("Zzqxvk")

    assert len(inner.calls) == 1
