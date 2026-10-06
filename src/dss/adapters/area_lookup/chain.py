"""An `AreaLookup` that tries several sources in order.

Core sees one lookup. Which sources stand behind it, and in what order, is a
wiring choice made in composition.
"""

from __future__ import annotations

from collections.abc import Sequence

from dss.ports.area_lookup import AreaLookup, AreaLookupUnavailable, AreaMatch


class ChainedAreaLookup:
    """Stops at the first source that returns a non-empty list."""

    def __init__(self, sources: Sequence[tuple[str, AreaLookup]]) -> None:
        self._sources = list(sources)

    async def resolve(self, name: str, region: str | None = None) -> list[AreaMatch]:
        for _, lookup in self._sources:
            try:
                matches = await lookup.resolve(name, region)
            except AreaLookupUnavailable:
                continue
            if matches:
                return matches
        return []
