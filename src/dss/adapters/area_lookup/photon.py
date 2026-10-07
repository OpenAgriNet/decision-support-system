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


def _parts(properties: dict[str, Any]) -> tuple[str, ...]:
    """The places above a feature, coarsest first."""

    parts = (
        properties.get("country"),
        properties.get("state"),
        properties.get("county"),
    )
    return tuple(part for part in parts if part)


def _to_match(feature: dict[str, Any]) -> AreaMatch:
    properties = feature["properties"]
    name = properties["name"]
    return AreaMatch(
        name=name,
        region=properties["countrycode"],
        within=tuple(part for part in _parts(properties) if part != name),
        geometry=Geometry(coordinates=feature["geometry"]["coordinates"]),
    )


def _inside_prefix(feature: dict[str, Any]) -> tuple[str, ...] | None:
    """If the feature sits inside a place with its own name (the town of
    Nakuru in Nakuru county), the places above that bigger place."""

    properties = feature["properties"]
    parts = _parts(properties)
    name = properties["name"]
    return parts[: parts.index(name)] if name in parts else None


def _without_containers(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop the bigger place when a town of the same name sits inside it.

    This is done here and not in core on purpose. Core keeps the bigger place
    when one sits inside another of the same name. That is right for the area
    file, where a block's point is a copy of its district's, so nothing is
    lost. Photon gives the town its own exact point, while the county's point
    is only a rough centre, from a few km to over 100 km from the town. Our
    provider search is a 25 km circle, so the county's point can miss the
    farmer. Core cannot tell which of two points is the exact one. The adapter
    can.
    """

    inside = {p for f in features if (p := _inside_prefix(f)) is not None}
    return [
        f
        for f in features
        if _inside_prefix(f) is not None or _to_match(f).within not in inside
    ]


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

    @property
    def country_codes(self) -> tuple[str, ...]:
        return self._country_codes

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
            features = [
                feature
                for feature in response.json()["features"]
                if _fold(feature["properties"].get("name", "")) == wanted
            ]
            matches = [_to_match(f) for f in _without_containers(features)]
        except (ValueError, KeyError, TypeError) as error:
            raise AreaLookupUnavailable("unreadable reply") from error
        # Same name, same surroundings: the farmer cannot tell them apart.
        # Keep the first.
        unique: dict[tuple[str, ...], AreaMatch] = {}
        for match in matches:
            unique.setdefault(match.within, match)
        return list(unique.values())
