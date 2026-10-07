"""Tier 2 — the Photon adapter, against recorded responses.

`httpx.MockTransport` serves the fixtures, so no socket is opened. The
fixtures were recorded once from the public demo server; it is never called
from tests.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from pathlib import Path

import anyio
import httpx
import pytest

from dss.adapters.area_lookup.photon import PhotonAreaLookup
from dss.core.shared.models import Geometry
from dss.ports.area_lookup import AreaLookupUnavailable, AreaMatch

FIXTURES = Path(__file__).parent / "fixtures"
BASE_URL = "http://photon.test"
_TIMEOUT = 5.0


def _photon_with(
    handler: Callable[[httpx.Request], httpx.Response]
    | Callable[[httpx.Request], Coroutine[None, None, httpx.Response]],
    *,
    country_codes: tuple[str, ...] = ("KE",),
    timeout_seconds: float = _TIMEOUT,
) -> PhotonAreaLookup:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return PhotonAreaLookup(
        client=client,
        base_url=BASE_URL,
        country_codes=country_codes,
        timeout_seconds=timeout_seconds,
    )


def _photon_returning(fixture: str, country_codes: tuple[str, ...]) -> PhotonAreaLookup:
    body = (FIXTURES / fixture).read_bytes()
    return _photon_with(
        lambda request: httpx.Response(200, content=body),
        country_codes=country_codes,
    )


async def test_the_request_filters_by_country_and_layer() -> None:
    """One `countrycode` per configured code, and only place-sized layers."""

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"features": []})

    photon = _photon_with(handler, country_codes=("KE", "UG"))

    await photon.resolve("Eldoret")

    query = seen[0].url.params
    assert query["q"] == "Eldoret"
    assert query.get_list("countrycode") == ["KE", "UG"]
    assert query.get_list("layer") == [
        "city",
        "district",
        "locality",
        "county",
        "state",
    ]


async def test_a_server_error_is_unavailable() -> None:
    """A 500 is not "no such place". The chain must tell the two apart."""

    photon = _photon_with(lambda request: httpx.Response(500))

    with pytest.raises(AreaLookupUnavailable):
        await photon.resolve("Eldoret")


async def test_a_body_that_is_not_json_is_unavailable() -> None:
    """A proxy can answer 200 with an error page. Hand-written: a bad body
    cannot be recorded from a healthy server."""

    photon = _photon_returning("photon_malformed.json", ("KE",))

    with pytest.raises(AreaLookupUnavailable):
        await photon.resolve("Eldoret")


async def test_a_slow_server_is_unavailable() -> None:
    """A place lookup is a hint. The turn must not wait on a slow geocoder."""

    async def slow(request: httpx.Request) -> httpx.Response:
        await anyio.sleep(1)
        return httpx.Response(200, content=b"{}")

    photon = _photon_with(slow, timeout_seconds=0.01)

    with pytest.raises(AreaLookupUnavailable):
        await photon.resolve("Eldoret")


async def test_a_refused_connection_is_unavailable() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    photon = _photon_with(refuse)

    with pytest.raises(AreaLookupUnavailable):
        await photon.resolve("Eldoret")


async def test_a_shared_name_gives_one_per_place() -> None:
    """Ten villages are named "Rampur". Three of them sit in Uttar Pradesh with
    nothing to tell them apart, so they count as one choice for the farmer."""

    photon = _photon_returning("photon_rampur.json", ("IN",))

    matches = await photon.resolve("Rampur")

    withins = [match.within for match in matches]
    assert len(withins) == 8
    assert len(set(withins)) == 8


async def test_a_near_name_is_not_a_match() -> None:
    """Photon returns places with similar names. We never guess: no exact
    name means the place is not found."""

    photon = _photon_returning("photon_eldoret.json", ("KE",))

    assert await photon.resolve("Eldor") == []


async def test_a_whole_region_is_marked_as_one() -> None:
    """Photon's `state` layer is a region: a state in India, a region in
    Uganda and Ghana. Core must never search around one point for it."""

    photon = _photon_returning("photon_central_region.json", ("UG", "GH"))

    matches = await photon.resolve("Central Region")

    assert [(m.within, m.is_region) for m in matches] == [
        (("Uganda",), True),
        (("Ghana",), True),
    ]


async def test_a_town_in_its_own_county_is_one_place() -> None:
    """Nakuru is a town, and the county around it is also called Nakuru. Photon
    returns both. Asking "which Nakuru?" has no real answer, and the town's
    point is the more exact one, so it stays."""

    photon = _photon_returning("photon_nakuru.json", ("KE",))

    matches = await photon.resolve("Nakuru")

    assert [match.geometry.coordinates for match in matches] == [
        [36.0712048, -0.2802724]
    ]


async def test_a_city_resolves_to_one_match() -> None:
    """Photon also returns a depot and two villages with similar names. Only
    the one named exactly "Eldoret" is the place the farmer meant."""

    photon = _photon_returning("photon_eldoret.json", ("KE",))

    assert await photon.resolve("Eldoret") == [
        AreaMatch(
            name="Eldoret",
            region="KE",
            within=("Kenya", "Uasin Gishu County", "Moiben"),
            geometry=Geometry(coordinates=[35.2715481, 0.5198329]),
        )
    ]
