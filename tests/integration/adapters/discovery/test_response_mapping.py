"""Contract tests for mapping on_discover responses.

Pure translation only — no business logic. A resource's own subjectCategories
matching or diverging from the index, expired validity, etc. are core's job,
not the adapter's.
"""

from __future__ import annotations

import json
from pathlib import Path

from dss.adapters.discovery.client import map_on_discover_response

FIXTURES = Path(__file__).parent / "fixtures"


def test_an_on_demand_resource_maps_to_a_provider_capability() -> None:
    response = json.loads((FIXTURES / "on_discover_response.json").read_text())

    result = map_on_discover_response(response, ask_indices=(0,))

    assert result.answers == {}
    assert result.failures == {}
    assert result.events == ()
    capabilities = result.capabilities[0]
    assert len(capabilities) == 1
    capability = capabilities[0]
    assert capability.provider_id == "mausamgram"
    assert capability.provider_name == "IMD Mausamgram NWP"
    assert capability.capability == "openagrinet:WeatherObservation"
    assert capability.resource_id == "res:mausamgram:point-forecast"


def test_the_same_result_is_keyed_under_every_ask_index() -> None:
    response = json.loads((FIXTURES / "on_discover_response.json").read_text())

    result = map_on_discover_response(response, ask_indices=(0, 2))

    assert result.capabilities[0] == result.capabilities[2]
