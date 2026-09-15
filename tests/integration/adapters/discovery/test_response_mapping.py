"""Contract tests for mapping on_discover responses.

Pure translation only — no business logic. A resource's own subjectCategories
matching or diverging from the index, expired validity, etc. are core's job,
not the adapter's.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dss.adapters.discovery.client import map_discover_response

FIXTURES = Path(__file__).parent / "fixtures"


def test_an_on_demand_resource_maps_to_a_provider_capability() -> None:
    response = json.loads((FIXTURES / "discover_response.json").read_text())

    result = map_discover_response(response, ask_indices=(0,))

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


def test_the_providers_descriptor_code_is_kept() -> None:
    """``on_discover`` carries a provider code the mapper used to discard.
    Nothing sends it yet — the real select request names the provider by id
    and name only — but discarding data the network gave us means it cannot
    be sent later without another round of discovery."""

    response = json.loads((FIXTURES / "discover_response.json").read_text())

    result = map_discover_response(response, ask_indices=(0,))

    assert result.capabilities[0][0].provider_code == "IMD-NWP-01"


def test_a_provider_with_no_descriptor_code_maps_to_none() -> None:
    """``code`` is not guaranteed — only ``name`` appears in every fixture."""

    response = json.loads((FIXTURES / "discover_response.json").read_text())
    provider = response["message"]["catalogs"][0]["provider"]
    del provider["descriptor"]["code"]

    result = map_discover_response(response, ask_indices=(0,))

    assert result.capabilities[0][0].provider_code is None


def test_a_resources_advertised_attributes_are_kept() -> None:
    """A provider advertises its own vocabulary in the catalog — MandiPrice
    names supportedCommodities, another pack names something else. Discarding
    it left the model guessing a commodity code with nothing to check it
    against.

    Kept as an opaque map, not as named fields: the DSS does not know what any
    pack advertises, and one pack's vocabulary must not enter a type four
    packs share.
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
                                "informationMode": "OnDemand",
                                "supportedCommodities": [
                                    {"code": "78", "name": "Tomato"}
                                ],
                            },
                        }
                    ],
                }
            ]
        }
    }

    result = map_discover_response(response, ask_indices=(0,))

    assert result.capabilities[0][0].advertised == {
        "supportedCommodities": [{"code": "78", "name": "Tomato"}]
    }


def test_the_same_result_is_keyed_under_every_ask_index() -> None:
    response = json.loads((FIXTURES / "discover_response.json").read_text())

    result = map_discover_response(response, ask_indices=(0, 2))

    assert result.capabilities[0] == result.capabilities[2]


def test_a_direct_resource_maps_to_a_discovered_answer() -> None:
    response = json.loads((FIXTURES / "discover_response_direct.json").read_text())

    result = map_discover_response(response, ask_indices=(0,))

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

    result = map_discover_response(response, ask_indices=(0,))

    validity = result.answers[0][0].validity
    assert validity is not None
    assert validity.starts_at == datetime.fromisoformat("2026-08-24T00:00:00+00:00")
    assert validity.ends_at == datetime.fromisoformat("2026-08-25T00:00:00+00:00")


def _direct_response_with_validity(validity: dict[str, Any]) -> dict[str, Any]:
    return {
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
                                "validity": validity,
                            },
                        }
                    ],
                }
            ]
        }
    }


def test_a_bare_end_date_stretches_to_the_end_of_that_day() -> None:
    """The spec allows a date-only endsAt, which means valid *through* that
    day. Truncating it to midnight would expire the answer a day early.
    """
    response = _direct_response_with_validity({"endsAt": "2026-08-26"})

    result = map_discover_response(response, ask_indices=(0,))

    validity = result.answers[0][0].validity
    assert validity is not None
    assert validity.ends_at == datetime(2026, 8, 26, 23, 59, 59, 999999, tzinfo=UTC)


def test_a_bare_start_date_stays_at_the_start_of_that_day() -> None:
    response = _direct_response_with_validity({"startsAt": "2026-08-26"})

    result = map_discover_response(response, ask_indices=(0,))

    validity = result.answers[0][0].validity
    assert validity is not None
    assert validity.starts_at == datetime(2026, 8, 26, 0, 0, tzinfo=UTC)


def test_a_resource_without_subject_categories_still_maps() -> None:
    """subjectCategories is optional on the wire. Core compares whatever is
    observed against the index, so absent means 'nothing observed', not a
    broken response.
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
                                "@type": "openagrinet:WeatherObservation",
                                "informationMode": "OnDemand",
                            },
                        }
                    ],
                }
            ]
        }
    }

    result = map_discover_response(response, ask_indices=(0,))

    assert result.capabilities[0][0].observed_categories == ()


def test_a_catalog_without_resources_maps_to_nothing() -> None:
    response = {
        "message": {
            "catalogs": [{"provider": {"id": "p", "descriptor": {"name": "P"}}}]
        }
    }

    result = map_discover_response(response, ask_indices=(0,))

    assert result.capabilities == {0: ()}
    assert result.answers == {0: ()}


def test_a_resource_without_an_information_mode_is_skipped() -> None:
    """Neither OnDemand nor Direct — nothing can be done with it, but it must
    not take the rest of the catalog down with it.
    """
    response = {
        "message": {
            "catalogs": [
                {
                    "provider": {"id": "p", "descriptor": {"name": "P"}},
                    "resources": [
                        {"id": "r", "resourceAttributes": {"@type": "openagrinet:X"}},
                        {
                            "id": "r2",
                            "resourceAttributes": {
                                "@type": "openagrinet:WeatherObservation",
                                "informationMode": "OnDemand",
                            },
                        },
                    ],
                }
            ]
        }
    }

    result = map_discover_response(response, ask_indices=(0,))

    assert len(result.capabilities[0]) == 1
    assert result.capabilities[0][0].resource_id == "r2"


def test_a_naive_timestamp_is_read_as_utc_with_its_time_untouched() -> None:
    """Core compares validity against a tz-aware now, so a naive value has to
    pick up a zone here or the comparison raises.
    """
    response = _direct_response_with_validity(
        {"startsAt": "2026-08-26T06:30:00", "endsAt": "2026-08-26T18:45:00"}
    )

    result = map_discover_response(response, ask_indices=(0,))

    validity = result.answers[0][0].validity
    assert validity is not None
    assert validity.starts_at == datetime(2026, 8, 26, 6, 30, tzinfo=UTC)
    assert validity.ends_at == datetime(2026, 8, 26, 18, 45, tzinfo=UTC)


def test_a_direct_resources_source_block_is_promoted() -> None:
    """A Direct answer never reaches `/select`, so it has to be named by the
    same rule here or one `sources[]` array mixes two naming schemes."""

    response = json.loads((FIXTURES / "discover_response_direct.json").read_text())

    answer = map_discover_response(response, ask_indices=(0,)).answers[0][0]

    assert answer.source_id == "source:agmarknet"
    assert answer.source_name == "AGMARKNET"
    assert answer.source_url is None


def test_a_direct_resource_relaying_another_source_keeps_both_names() -> None:
    """The fixture's provider and source happen to share a name. This is the
    case that matters: an aggregator citing someone else's data."""

    response = json.loads((FIXTURES / "discover_response_direct.json").read_text())
    catalog = response["message"]["catalogs"][0]
    catalog["provider"]["descriptor"]["name"] = "Krishi Knowledge Base"
    catalog["resources"][0]["resourceAttributes"]["source"] = {
        "sourceId": "source:agmarknet",
        "sourceName": "AGMARKNET",
        "sourceUri": "https://agmarknet.gov.in",
    }

    answer = map_discover_response(response, ask_indices=(0,)).answers[0][0]

    assert answer.provider_name == "Krishi Knowledge Base"
    assert answer.source_name == "AGMARKNET"
    assert answer.source_url == "https://agmarknet.gov.in"


def test_a_direct_resource_without_a_source_block_maps_to_none() -> None:
    response = json.loads((FIXTURES / "discover_response_direct.json").read_text())
    del response["message"]["catalogs"][0]["resources"][0]["resourceAttributes"][
        "source"
    ]

    answer = map_discover_response(response, ask_indices=(0,)).answers[0][0]

    assert (answer.source_id, answer.source_name, answer.source_url) == (
        None,
        None,
        None,
    )
