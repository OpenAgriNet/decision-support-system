"""Tier 1 — assembling resourceAttributes for a /select call.

Structural fields (@context, @type, subjectCategories, location) are built
here from discovery data and the turn; the model's own resource_attributes
(e.g. topics, a resolved commodity code) are merged on top. No network, no
framework.
"""

from __future__ import annotations

import json
from pathlib import Path

from dss.adapters.discovery.client import _advertised
from dss.core.planner.resource_attributes import build_resource_attributes
from dss.core.provider_discovery.models import ProviderCapability
from dss.core.shared.models import Geometry, Location, UserTurn

_SCHEMA_CONTEXT_INDEX = {
    "openagrinet:MandiPrice": "https://schemas.openagrinet.global/schema/MandiPrice/v0.1/context.jsonld",
}
_SCHEMA_BASE_URL = "https://schemas.openagrinet.global/schema"
# MandiPrice's own filterable paths, as `parse_domain_schema` hands them over.
_MANDI_FILTERABLE = (
    "informationMode",
    "subjectCategories",
    "supportedCommodities[].code",
    "supportedPriceFields",
    "commodity.code",
    "commodityGroup",
    "variety",
    "market.marketCode",
    "market.state",
    "arrivalDate",
)


def _capability(advertised: dict | None = None) -> ProviderCapability:
    return ProviderCapability(
        provider_id="agmarknet",
        provider_name="Agmarknet",
        capability="openagrinet:MandiPrice",
        resource_id="res:agmarknet:daily-price",
        observed_categories=("Market",),
        advertised=advertised or {},
    )


def _turn(*, location: Location | None) -> UserTurn:
    return UserTurn(
        original_query="price of potato",
        enriched_query="price of potato",
        transaction_id="txn-1",
        session_id="s-1",
        source_lang="en",
        target_lang="en",
        channel="web",
        location=location,
    )


def test_builds_context_type_and_subject_categories() -> None:
    resource_attributes = build_resource_attributes(
        capability=_capability(),
        turn=_turn(location=None),
        model_filled={},
        schema_context_index=_SCHEMA_CONTEXT_INDEX,
        filterable=_MANDI_FILTERABLE,
    )

    assert resource_attributes["@context"] == (
        "https://schemas.openagrinet.global/schema/MandiPrice/v0.1/context.jsonld"
    )
    assert resource_attributes["@type"] == "openagrinet:MandiPrice"
    assert resource_attributes["subjectCategories"] == ["Market"]


def test_omits_location_when_turn_has_none() -> None:
    resource_attributes = build_resource_attributes(
        capability=_capability(),
        turn=_turn(location=None),
        model_filled={},
        schema_context_index=_SCHEMA_CONTEXT_INDEX,
        filterable=_MANDI_FILTERABLE,
    )

    assert "location" not in resource_attributes


def test_includes_location_from_turn_geometry() -> None:
    """`location` is a Beckn Location, which wraps the geometry under `geo`.

    A bare GeoJSON Point here is rejected: every pack's `location` resolves
    to `CompleteLocation`, whose `geo` is required.
    """

    location = Location(geometry=Geometry(coordinates=[72.93, 22.56]))
    resource_attributes = build_resource_attributes(
        capability=_capability(),
        turn=_turn(location=location),
        model_filled={},
        schema_context_index=_SCHEMA_CONTEXT_INDEX,
        filterable=_MANDI_FILTERABLE,
    )

    assert resource_attributes["location"] == {
        "geo": {"type": "Point", "coordinates": [72.93, 22.56]}
    }


def test_merges_model_filled_fields_on_top() -> None:
    resource_attributes = build_resource_attributes(
        capability=_capability(),
        turn=_turn(location=None),
        model_filled={"commodity": {"code": "PADDY", "name": "Paddy"}},
        schema_context_index=_SCHEMA_CONTEXT_INDEX,
        filterable=_MANDI_FILTERABLE,
    )

    assert resource_attributes["commodity"] == {"code": "PADDY", "name": "Paddy"}


def test_model_filled_cannot_override_a_structural_field() -> None:
    resource_attributes = build_resource_attributes(
        capability=_capability(),
        turn=_turn(location=None),
        model_filled={"@type": "something-else"},
        schema_context_index=_SCHEMA_CONTEXT_INDEX,
        filterable=_MANDI_FILTERABLE,
    )

    assert resource_attributes["@type"] == "openagrinet:MandiPrice"


def test_the_discovered_attributes_are_echoed_back() -> None:
    """A /select is judged against the resource the provider advertised, so
    what discovery returned is the base of the request rather than a set of
    values to rebuild from scratch.

    `market` is the case that forced this: its schema requires `marketName`,
    which `profile.json` never lists as filterable, so the model could not
    supply it and the provider rejected every call. Echoing the discovered
    object carries it through untouched.
    """

    resource_attributes = build_resource_attributes(
        capability=_capability(
            advertised={
                "market": {
                    "marketName": "Rahuri APMC",
                    "district": "338",
                    "state": "MH",
                },
                "supportedPriceFields": ["Minimum", "Maximum", "Modal"],
            }
        ),
        turn=_turn(location=None),
        model_filled={},
        schema_context_index=_SCHEMA_CONTEXT_INDEX,
        filterable=_MANDI_FILTERABLE,
    )

    assert resource_attributes["market"] == {
        "marketName": "Rahuri APMC",
        "district": "338",
        "state": "MH",
    }
    assert resource_attributes["supportedPriceFields"] == [
        "Minimum",
        "Maximum",
        "Modal",
    ]


def test_the_model_narrows_a_discovered_list() -> None:
    """The provider advertises every commodity it serves; the farmer asked
    about one. The model's value replaces the advertised list rather than
    adding to it, so the call asks for Onion alone."""

    resource_attributes = build_resource_attributes(
        capability=_capability(
            advertised={
                "supportedCommodities": [
                    {"code": "23", "name": "Onion"},
                    {"code": "78", "name": "Tomato"},
                ]
            }
        ),
        turn=_turn(location=None),
        model_filled={"supportedCommodities": [{"code": "23", "name": "Onion"}]},
        schema_context_index=_SCHEMA_CONTEXT_INDEX,
        filterable=_MANDI_FILTERABLE,
    )

    assert resource_attributes["supportedCommodities"] == [
        {"code": "23", "name": "Onion"}
    ]


def test_a_provider_fact_is_not_echoed_as_a_filter() -> None:
    """A resource advertises two kinds of thing side by side: values a caller
    may filter on (`supportedParameters`) and facts about the provider
    (`forecastHorizon: P5D`, `updateFrequency: PT12H`).

    Only the first belongs in a select. Echoing the second sends a fact back as
    a filter criterion it never was, and a pack declaring
    `additionalProperties: false` rejects the whole call for it.
    """

    resource_attributes = build_resource_attributes(
        capability=_capability(
            advertised={
                "supportedParameters": ["Rainfall", "Temperature"],
                "forecastHorizon": "P5D",
                "updateFrequency": "PT12H",
            }
        ),
        turn=_turn(location=None),
        model_filled={},
        schema_context_index=_SCHEMA_CONTEXT_INDEX,
        filterable=("supportedParameters", "location"),
    )

    assert resource_attributes["supportedParameters"] == ["Rainfall", "Temperature"]
    assert "forecastHorizon" not in resource_attributes
    assert "updateFrequency" not in resource_attributes


def test_nothing_outside_the_filterable_set_reaches_the_request() -> None:
    """The guard, driven by a real `on_discover` body rather than a handwritten
    one.

    Both tests above build `advertised` by hand, which is how echoing a
    provider's own facts (`forecastHorizon`, `updateFrequency`) went unnoticed:
    they assert what the author expected the network to send. This runs the
    recorded response through the same `_advertised` the adapter uses, and
    asserts the negative — that no key outside the pack's filterable set
    survives into the request.
    """

    response = json.loads(
        (
            Path(__file__).parents[3]
            / "integration"
            / "adapters"
            / "discovery"
            / "fixtures"
            / "discover_response.json"
        ).read_text()
    )
    weather = next(
        resource["resourceAttributes"]
        for catalog in response["message"]["catalogs"]
        for resource in catalog.get("resources", ())
        if resource["resourceAttributes"]["@type"] == "openagrinet:WeatherObservation"
    )
    advertised = _advertised(weather)
    # the fixture really does carry provider facts, or this proves nothing
    assert "forecastHorizon" in advertised

    filterable = ("supportedObservationTypes", "supportedParameters", "location")
    resource_attributes = build_resource_attributes(
        capability=_capability(advertised=advertised),
        turn=_turn(location=None),
        model_filled={},
        schema_context_index=_SCHEMA_CONTEXT_INDEX,
        filterable=filterable,
    )

    structural = {"@context", "@type", "informationMode", "subjectCategories"}
    unexpected = set(resource_attributes) - set(filterable) - structural
    assert not unexpected, f"non-filterable fields reached the request: {unexpected}"


def test_a_discovered_location_wins_over_the_turns_geometry() -> None:
    """Where a pack's `location` identifies the resource rather than the query,
    the advertised value stands.

    `AgricultureFacility.location` says so in words — "verified facility
    geometry ... do not populate it with the search origin or another inferred
    point". Sending the farmer's coordinates there would claim the facility is
    wherever they happen to be asking from.
    """

    facility_geometry = {"geo": {"type": "Point", "coordinates": [72.83, 18.94]}}

    resource_attributes = build_resource_attributes(
        capability=_capability(advertised={"location": facility_geometry}),
        turn=_turn(location=Location(geometry=Geometry(coordinates=[74.06, 18.57]))),
        model_filled={},
        schema_context_index=_SCHEMA_CONTEXT_INDEX,
        filterable=("location",),
    )

    assert resource_attributes["location"] == facility_geometry


def test_the_turns_geometry_fills_a_location_nobody_supplied() -> None:
    """An OnDemand weather resource advertises no `location` — there is no
    fixed point until someone asks — so the turn's geometry is what says which
    place the forecast is for."""

    resource_attributes = build_resource_attributes(
        capability=_capability(advertised={"supportedParameters": ["Rainfall"]}),
        turn=_turn(location=Location(geometry=Geometry(coordinates=[74.06, 18.57]))),
        model_filled={},
        schema_context_index=_SCHEMA_CONTEXT_INDEX,
        filterable=("location", "supportedParameters"),
    )

    assert resource_attributes["location"] == {
        "geo": {"type": "Point", "coordinates": [74.06, 18.57]}
    }


def test_narrowing_an_advertised_list_keeps_the_whole_item() -> None:
    """The model names which advertised item it wants, not a replacement for it.

    It can only send the filterable field — `supportedCommodities[].code` — so
    replacing the list outright dropped every other part of the item, and the
    provider got `{"code": "23"}` where it had advertised
    `{"code": "23", "name": "Onion"}`.
    """

    resource_attributes = build_resource_attributes(
        capability=_capability(
            advertised={
                "supportedCommodities": [
                    {"code": "10", "name": "Groundnut"},
                    {"code": "23", "name": "Onion"},
                ]
            }
        ),
        turn=_turn(location=None),
        model_filled={"supportedCommodities": [{"code": "23"}]},
        schema_context_index=_SCHEMA_CONTEXT_INDEX,
        filterable=("supportedCommodities[].code",),
    )

    assert resource_attributes["supportedCommodities"] == [
        {"code": "23", "name": "Onion"}
    ]


def test_an_authored_list_is_not_matched_against_anything() -> None:
    """`parameters` is never advertised — no pack carries it on an OnDemand
    resource — so the model composes it from the question and there is nothing
    to select from. It passes through as written."""

    resource_attributes = build_resource_attributes(
        capability=_capability(advertised={"supportedParameters": ["Rainfall"]}),
        turn=_turn(location=None),
        model_filled={"parameters": [{"parameter": "Rainfall"}]},
        schema_context_index=_SCHEMA_CONTEXT_INDEX,
        filterable=("parameters[].parameter", "supportedParameters"),
    )

    assert resource_attributes["parameters"] == [{"parameter": "Rainfall"}]


def test_an_item_matching_nothing_advertised_is_sent_as_written() -> None:
    """The model named something the provider did not advertise. That is the
    provider's call to refuse, not ours to drop silently."""

    resource_attributes = build_resource_attributes(
        capability=_capability(
            advertised={"supportedCommodities": [{"code": "23", "name": "Onion"}]}
        ),
        turn=_turn(location=None),
        model_filled={"supportedCommodities": [{"code": "99"}]},
        schema_context_index=_SCHEMA_CONTEXT_INDEX,
        filterable=("supportedCommodities[].code",),
    )

    assert resource_attributes["supportedCommodities"] == [{"code": "99"}]
