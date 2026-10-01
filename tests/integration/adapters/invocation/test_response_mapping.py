"""Contract tests for mapping select responses.

Pure translation only — no business logic.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from dss.adapters.invocation.client import map_select_response

FIXTURES = Path(__file__).parent / "fixtures"


def test_maps_the_response_into_a_discovered_answer() -> None:
    response = json.loads((FIXTURES / "select_response.json").read_text())

    (answer,) = map_select_response(
        response, provider_id="mausamgram", provider_name="IMD Mausamgram NWP"
    )

    assert answer.provider_id == "mausamgram"
    assert answer.provider_name == "IMD Mausamgram NWP"
    assert answer.capability == "openagrinet:WeatherObservation"
    assert answer.resource_id == "res:mausamgram:forecast:2026-08-26"


def test_attributes_carry_the_real_data() -> None:
    response = json.loads((FIXTURES / "select_response.json").read_text())

    (answer,) = map_select_response(
        response, provider_id="mausamgram", provider_name="IMD Mausamgram NWP"
    )

    [resource] = answer.attributes["resources"]
    assert resource["observationType"] == "Forecast"
    assert len(resource["parameters"]) == 7
    assert resource["parameters"][0] == {
        "parameter": "Rainfall",
        "aggregation": "Total",
        "unit": "mm",
        "value": 0.84,
    }


def test_every_resource_in_the_response_reaches_the_answer() -> None:
    """A forecast sends one resource per day. Keeping only the first told the
    farmer we had one day of a five-day forecast."""

    response = json.loads(
        (FIXTURES / "select_response_five_day_forecast.json").read_text()
    )

    (answer,) = map_select_response(
        response, provider_id="mausamgram", provider_name="IMD Mausamgram"
    )

    assert [r["id"] for r in answer.attributes["resources"]] == [
        "res:mausamgram:forecast:2026-09-30",
        "res:mausamgram:forecast:2026-10-01",
        "res:mausamgram:forecast:2026-10-02",
        "res:mausamgram:forecast:2026-10-03",
        "res:mausamgram:forecast:2026-10-04",
    ]


def test_validity_stays_on_each_resource_not_the_answer() -> None:
    """One validity cannot describe five days. Nothing filters a select
    answer on it, and the model reads each day's own."""

    response = json.loads(
        (FIXTURES / "select_response_five_day_forecast.json").read_text()
    )

    (answer,) = map_select_response(
        response, provider_id="mausamgram", provider_name="IMD Mausamgram"
    )

    assert answer.validity is None
    assert answer.attributes["resources"][4]["validity"] == {
        "startsAt": "2026-10-04T00:00:00+05:30",
        "endsAt": "2026-10-04T23:59:59+05:30",
    }


def test_the_payloads_source_block_is_promoted() -> None:
    """`Source.name` is the originator, so the block has to leave `attributes`
    as typed fields — `core/` never learns the pack wire shape."""

    response = json.loads((FIXTURES / "select_response.json").read_text())

    (answer,) = map_select_response(
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

    (answer,) = map_select_response(
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

    (answer,) = map_select_response(
        response, provider_id="mausamgram", provider_name="IMD"
    )

    assert answer.source_url == "https://mausam.imd.gov.in"


def test_the_source_block_stays_in_attributes_as_well() -> None:
    """`Result.data` is `resourceAttributes` verbatim — promotion copies, it
    does not move."""

    response = json.loads((FIXTURES / "select_response.json").read_text())

    (answer,) = map_select_response(
        response, provider_id="mausamgram", provider_name="IMD"
    )

    [resource] = answer.attributes["resources"]
    assert resource["source"]["sourceName"] == "IMD Mausamgram NWP"


def test_resources_are_grouped_by_the_document_they_came_from() -> None:
    """A knowledge provider answers one call with passages from several
    documents. Lumping them into one answer made every passage inherit the
    first one's source, so half the advice was cited to a document it never
    came from."""

    response = json.loads((FIXTURES / "select_response.json").read_text())
    resources = response["message"]["contract"]["commitments"][0]["resources"]
    other = deepcopy(resources[0])
    other["id"] = "res:icar:advisory"
    other["resourceAttributes"]["source"] = {
        "sourceId": "icar",
        "sourceName": "ICAR Advisories",
    }
    resources.append(other)

    answers = map_select_response(
        response, provider_id="bharat-vistaar", provider_name="Knowledge retrieval"
    )

    assert [answer.source_name for answer in answers] == [
        "IMD Mausamgram NWP",
        "ICAR Advisories",
    ]
    assert [len(answer.attributes["resources"]) for answer in answers] == [1, 1]


def test_one_documents_resources_stay_in_one_answer() -> None:
    """A five-day forecast is five resources from one source. It must stay a
    single answer, or the composer sees five sources that all agree."""

    response = json.loads(
        (FIXTURES / "select_response_five_day_forecast.json").read_text()
    )

    answers = map_select_response(
        response, provider_id="mausamgram", provider_name="IMD Mausamgram NWP"
    )

    assert len(answers) == 1
    assert len(answers[0].attributes["resources"]) == 5


def test_resources_across_every_commitment_are_kept() -> None:
    response = json.loads((FIXTURES / "select_response.json").read_text())
    commitments = response["message"]["contract"]["commitments"]
    second = deepcopy(commitments[0])
    second["resources"][0]["id"] = "res:second-offer"
    commitments.append(second)

    answers = map_select_response(
        response, provider_id="mausamgram", provider_name="IMD Mausamgram NWP"
    )

    # one source across both commitments, so one answer holding both resources
    assert len(answers) == 1
    assert [r["id"] for r in answers[0].attributes["resources"]] == [
        "res:mausamgram:forecast:2026-08-26",
        "res:second-offer",
    ]


def test_a_commitment_with_no_resources_is_not_an_answer() -> None:
    response = json.loads((FIXTURES / "select_response.json").read_text())
    response["message"]["contract"]["commitments"][0]["resources"] = []

    assert (
        map_select_response(response, provider_id="mausamgram", provider_name="IMD")
        == []
    )
