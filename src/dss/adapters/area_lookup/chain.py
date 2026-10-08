"""An `AreaLookup` that tries several sources in order.

Core sees one lookup. Which sources stand behind it, and in what order, is a
wiring choice made in composition.
"""

from __future__ import annotations

from collections.abc import Sequence

from dss.adapters.observability.metrics import record_area_lookup
from dss.core.shared.models import Geometry
from dss.ports.area_lookup import AreaLookup, AreaLookupUnavailable, AreaMatch


class ChainedAreaLookup:
    """Stops at the first source that returns a non-empty list."""

    def __init__(self, sources: Sequence[tuple[str, AreaLookup]]) -> None:
        self._sources = list(sources)

    @property
    def sources(self) -> list[tuple[str, AreaLookup]]:
        return list(self._sources)

    async def resolve(self, name: str, region: str | None = None) -> list[AreaMatch]:
        fallback: list[AreaMatch] = []
        for source, lookup in self._sources:
            try:
                matches = await lookup.resolve(name, region)
            except AreaLookupUnavailable:
                record_area_lookup(source=source, outcome="error")
                continue
            if matches:
                record_area_lookup(source=source, outcome="hit")
                # A guess ("Kanha Chatti" for "Kanha") does not end the search:
                # a later source may have the exact name. Kept if none does.
                if all(match.is_guess for match in matches):
                    fallback = fallback or matches
                    continue
                return matches
            record_area_lookup(source=source, outcome="miss")
        return fallback

    async def nearest(self, point: Geometry, max_km: float) -> AreaMatch | None:
        for _, lookup in self._sources:
            try:
                match = await lookup.nearest(point, max_km)
            except AreaLookupUnavailable:
                continue
            if match is not None:
                return match
        return None
