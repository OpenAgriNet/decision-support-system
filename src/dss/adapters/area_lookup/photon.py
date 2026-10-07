"""An `AreaLookup` over a Photon geocoder (OpenStreetMap data).

Photon ranks by closeness, not by what the farmer said, so the top result can
be a confident wrong place. This adapter keeps only features named exactly as
asked. Core then asks the farmer when two remain.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import anyio
import httpx

from dss.adapters.observability.tracing import open_span
from dss.core.shared.models import Geometry
from dss.ports.area_lookup import AreaLookupUnavailable, AreaMatch

_LIMIT = "10"
# Village-sized places live in `locality` and `district`. Streets, houses and
# shops are never a place a farmer names.
_LAYERS = ("city", "district", "locality", "county", "state")


def _fold(text: str) -> str:
    return " ".join(text.split()).casefold()


def _to_match(feature: dict[str, Any]) -> AreaMatch:
    properties = feature["properties"]
    name = properties["name"]
    parts = (
        properties.get("country"),
        properties.get("state"),
        properties.get("county"),
    )
    return AreaMatch(
        name=name,
        region=properties["countrycode"],
        within=tuple(part for part in parts if part and part != name),
        geometry=Geometry(coordinates=feature["geometry"]["coordinates"]),
    )


class PhotonAreaLookup:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str,
        country_codes: Sequence[str],
        timeout_seconds: float,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._country_codes = tuple(country_codes)
        self._timeout_seconds = timeout_seconds

    async def resolve(self, name: str, region: str | None = None) -> list[AreaMatch]:
        # `region` is where the farmer is. The country filter comes from
        # settings, so the hint is not used here.
        # The name is not an attribute: a place name can point to a person.
        with open_span(
            "dss.area_lookup.photon",
            attributes={
                "name_length": len(name),
                "country_codes": ",".join(self._country_codes),
            },
        ) as span:
            try:
                matches = await self._lookup(name)
            except AreaLookupUnavailable:
                span.set_attribute("outcome", "error")
                raise
            span.set_attribute("result_count", len(matches))
            span.set_attribute("outcome", "hit" if matches else "miss")
            return matches

    async def _lookup(self, name: str) -> list[AreaMatch]:
        params = httpx.QueryParams(
            [
                ("q", name),
                ("limit", _LIMIT),
                ("lang", "en"),
                *(("layer", layer) for layer in _LAYERS),
                *(("countrycode", code) for code in self._country_codes),
            ]
        )
        try:
            with anyio.fail_after(self._timeout_seconds):
                response = await self._client.get(
                    f"{self._base_url}/api", params=params
                )
            response.raise_for_status()
        except (httpx.HTTPError, TimeoutError) as error:
            raise AreaLookupUnavailable(str(error)) from error
        wanted = _fold(name)
        try:
            matches = [
                _to_match(feature)
                for feature in response.json()["features"]
                if _fold(feature["properties"].get("name", "")) == wanted
            ]
        except (ValueError, KeyError, TypeError) as error:
            raise AreaLookupUnavailable("unreadable reply") from error
        # Same name, same surroundings: the farmer cannot tell them apart.
        # Keep the first.
        unique: dict[tuple[str, ...], AreaMatch] = {}
        for match in matches:
            unique.setdefault(match.within, match)
        return list(unique.values())
