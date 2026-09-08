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
