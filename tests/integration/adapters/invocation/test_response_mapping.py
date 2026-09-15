"""Contract tests for mapping select responses.

Pure translation only — no business logic.
"""

from __future__ import annotations

import json
from pathlib import Path

from dss.adapters.invocation.client import map_select_response

FIXTURES = Path(__file__).parent / "fixtures"


def test_maps_the_response_into_a_discovered_answer() -> None:
    response = json.loads((FIXTURES / "select_response.json").read_text())

    answer = map_select_response(
        response, provider_id="mausamgram", provider_name="IMD Mausamgram NWP"
    )

    assert answer.provider_id == "mausamgram"
    assert answer.provider_name == "IMD Mausamgram NWP"
    assert answer.capability == "openagrinet:WeatherObservation"
    assert answer.resource_id == "res:mausamgram:forecast:2026-08-26"


def test_attributes_carry_the_real_data() -> None:
    response = json.loads((FIXTURES / "select_response.json").read_text())

    answer = map_select_response(
        response, provider_id="mausamgram", provider_name="IMD Mausamgram NWP"
    )

    assert answer.attributes["observationType"] == "Forecast"
    assert len(answer.attributes["parameters"]) == 7
    assert answer.attributes["parameters"][0] == {
        "parameter": "Rainfall",
        "aggregation": "Total",
        "unit": "mm",
        "value": 0.84,
    }


def test_validity_is_parsed_and_tz_aware() -> None:
    response = json.loads((FIXTURES / "select_response.json").read_text())

    answer = map_select_response(
        response, provider_id="mausamgram", provider_name="IMD Mausamgram NWP"
    )

    assert answer.validity is not None
    assert answer.validity.starts_at.isoformat() == "2026-08-26T00:00:00+00:00"
    assert answer.validity.ends_at.isoformat() == "2026-08-26T23:59:59+00:00"


def test_the_payloads_source_block_is_promoted() -> None:
    """`Source.name` is the originator, so the block has to leave `attributes`
    as typed fields — `core/` never learns the pack wire shape."""

    response = json.loads((FIXTURES / "select_response.json").read_text())

    answer = map_select_response(
        response, provider_id="aws-network", provider_name="Weather Station Network"
    )

    assert answer.source_id == "mausamgram"
    assert answer.source_name == "IMD Mausamgram NWP"
    assert answer.source_url is None  # the fixture carries no sourceUri
    assert answer.provider_name == "Weather Station Network"


def test_a_response_without_a_source_block_maps_to_none() -> None:
    response = json.loads((FIXTURES / "select_response.json").read_text())
    resource = response["message"]["contract"]["commitments"][0]["resources"][0]
    del resource["resourceAttributes"]["source"]

    answer = map_select_response(
        response, provider_id="mausamgram", provider_name="IMD"
    )

    assert (answer.source_id, answer.source_name, answer.source_url) == (
        None,
        None,
        None,
    )


def test_a_source_uri_is_promoted_when_a_farmer_can_open_it() -> None:
    response = json.loads((FIXTURES / "select_response.json").read_text())
    resource = response["message"]["contract"]["commitments"][0]["resources"][0]
    resource["resourceAttributes"]["source"]["sourceUri"] = "https://mausam.imd.gov.in"

    answer = map_select_response(
        response, provider_id="mausamgram", provider_name="IMD"
    )

    assert answer.source_url == "https://mausam.imd.gov.in"


def test_the_source_block_stays_in_attributes_as_well() -> None:
    """`Result.data` is `resourceAttributes` verbatim — promotion copies, it
    does not move."""

    response = json.loads((FIXTURES / "select_response.json").read_text())

    answer = map_select_response(
        response, provider_id="mausamgram", provider_name="IMD"
    )

    assert answer.attributes["source"]["sourceName"] == "IMD Mausamgram NWP"
