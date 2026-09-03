"""Contract tests for mapping on_discover responses.

Pure translation only — no business logic. A resource's own subjectCategories
matching or diverging from the index, expired validity, etc. are core's job,
not the adapter's.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from dss.adapters.discovery.client import map_on_discover_response

FIXTURES = Path(__file__).parent / "fixtures"


def test_an_on_demand_resource_maps_to_a_provider_capability() -> None:
    response = json.loads((FIXTURES / "on_discover_response.json").read_text())

    result = map_on_discover_response(response, ask_indices=(0,))

    assert result.answers == {0: ()}
    assert result.failures == {0: ()}
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


def test_a_direct_resource_maps_to_a_discovered_answer() -> None:
    response = json.loads((FIXTURES / "on_discover_response_direct.json").read_text())

    result = map_on_discover_response(response, ask_indices=(0,))

    assert result.capabilities == {0: ()}
    assert result.failures == {0: ()}
    assert result.events == ()
    answers = result.answers[0]
    assert len(answers) == 1
    answer = answers[0]
    assert answer.provider_id == "agmarknet"
    assert answer.provider_name == "AGMARKNET"
    assert answer.capability == "openagrinet:MandiPrice"
    assert answer.resource_id == "res:agmarknet:onion-lasalgaon"
    assert answer.attributes["commodity"] == {"name": "Onion", "code": "ONION"}
    assert answer.attributes["prices"]["modal"] == 2200
    assert answer.validity is None


def test_a_direct_resource_with_validity_parses_it() -> None:
    """No real sample carries a validity block — this tests the mapping
    function's own logic against a schema-legal shape, not a real response.
    """
    response = {
        "message": {
            "catalogs": [
                {
                    "provider": {"id": "p", "descriptor": {"name": "P"}},
                    "resources": [
                        {
                            "id": "r",
                            "resourceAttributes": {
                                "@type": "openagrinet:MandiPrice",
                                "informationMode": "Direct",
                                "validity": {
                                    "startsAt": "2026-08-24T00:00:00+00:00",
                                    "endsAt": "2026-08-25T00:00:00+00:00",
                                },
                            },
                        }
                    ],
                }
            ]
        }
    }

    result = map_on_discover_response(response, ask_indices=(0,))

    validity = result.answers[0][0].validity
    assert validity is not None
    assert validity.starts_at == datetime.fromisoformat("2026-08-24T00:00:00+00:00")
    assert validity.ends_at == datetime.fromisoformat("2026-08-25T00:00:00+00:00")
