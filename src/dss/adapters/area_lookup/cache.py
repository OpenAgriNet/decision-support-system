"""A cache in front of a slow place source, such as Photon.

A farmer asks about the same village many times, and a place does not move.
`async-lru` does the keeping. It also makes simultaneous identical questions
share one call, which a plain dict would not.
"""

from __future__ import annotations

from async_lru import alru_cache

from dss.ports.area_lookup import AreaLookup, AreaMatch


class CachedAreaLookup:
    def __init__(
        self, inner: AreaLookup, *, ttl_seconds: float, max_entries: int
    ) -> None:
        self._inner = inner
        # Built per instance, so each cache has its own size and TTL. Answers
        # that are empty are kept, errors are not: a failed call is not an answer.
        self._cached = alru_cache(maxsize=max_entries, ttl=ttl_seconds)(self._ask)

    async def _ask(self, name: str, region: str | None) -> list[AreaMatch]:
        return await self._inner.resolve(name, region)

    async def resolve(self, name: str, region: str | None = None) -> list[AreaMatch]:
        # The cache hands back the list it stored, so callers get a copy.
        return list(await self._cached(" ".join(name.split()), region))
