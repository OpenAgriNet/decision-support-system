"""Tier 1 — assembling resourceAttributes for a /select call.

Structural fields (@context, @type, subjectCategories, location) are built
here from the schema pack, the ask and the turn; the model's own
resource_attributes (e.g. topics, a resolved commodity code) are merged on
top. No network, no framework.
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
        subject_category="Market",
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
        subject_category="Market",
        capability=_capability(),
        turn=_turn(location=None),
        model_filled={},
        schema_context_index=_SCHEMA_CONTEXT_INDEX,
        filterable=_MANDI_FILTERABLE,
    )

    assert "location" not in resource_attributes


def test_includes_location_from_turn_geometry() -> None:
    """`location` is a Beckn Location, which wraps the geometry under `geo`.

    A bare GeoJSON Point here is rejected: a pack's `location` resolves to
    `CompleteLocation`, whose `geo` is required.

    WeatherObservation's paths, because it is the one pack that declares a
    top-level `location` — an OnDemand forecast has no fixed point until
    someone asks, so the turn's geometry is what names the place.
    """

    location = Location(geometry=Geometry(coordinates=[72.93, 22.56]))
    resource_attributes = build_resource_attributes(
        subject_category="Market",
        capability=_capability(),
        turn=_turn(location=location),
        model_filled={},
        schema_context_index=_SCHEMA_CONTEXT_INDEX,
        filterable=("location.geo", "supportedParameters"),
        declared=("location", "supportedParameters"),
    )

    assert resource_attributes["location"] == {
        "geo": {"type": "Point", "coordinates": [72.93, 22.56]}
    }


def test_merges_model_filled_fields_on_top() -> None:
    resource_attributes = build_resource_attributes(
        subject_category="Market",
        capability=_capability(),
        turn=_turn(location=None),
        model_filled={"commodity": {"code": "PADDY", "name": "Paddy"}},
        schema_context_index=_SCHEMA_CONTEXT_INDEX,
        filterable=_MANDI_FILTERABLE,
    )

    assert resource_attributes["commodity"] == {"code": "PADDY", "name": "Paddy"}


def test_model_filled_cannot_override_a_structural_field() -> None:
    resource_attributes = build_resource_attributes(
        subject_category="Market",
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
        subject_category="Market",
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
        subject_category="Market",
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
        subject_category="Market",
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
        subject_category="Market",
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
        subject_category="Market",
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
        subject_category="Market",
        capability=_capability(advertised={"supportedParameters": ["Rainfall"]}),
        turn=_turn(location=Location(geometry=Geometry(coordinates=[74.06, 18.57]))),
        model_filled={},
        schema_context_index=_SCHEMA_CONTEXT_INDEX,
        filterable=("location", "supportedParameters"),
        declared=("location", "supportedParameters"),
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
        subject_category="Market",
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
        subject_category="Market",
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
        subject_category="Market",
        capability=_capability(
            advertised={"supportedCommodities": [{"code": "23", "name": "Onion"}]}
        ),
        turn=_turn(location=None),
        model_filled={"supportedCommodities": [{"code": "99"}]},
        schema_context_index=_SCHEMA_CONTEXT_INDEX,
        filterable=("supportedCommodities[].code",),
    )

    assert resource_attributes["supportedCommodities"] == [{"code": "99"}]


def test_a_scalar_list_is_left_alone() -> None:
    """`supportedPriceFields` is a list of plain strings, not of objects. There
    are no items to select between, so the model's value stands."""

    resource_attributes = build_resource_attributes(
        subject_category="Market",
        capability=_capability(
            advertised={"supportedPriceFields": ["Minimum", "Maximum", "Modal"]}
        ),
        turn=_turn(location=None),
        model_filled={"supportedPriceFields": ["Modal"]},
        schema_context_index=_SCHEMA_CONTEXT_INDEX,
        filterable=("supportedPriceFields",),
    )

    assert resource_attributes["supportedPriceFields"] == ["Modal"]


def test_a_value_of_a_different_kind_is_left_alone() -> None:
    """Neither selection nor merging applies when the two sides are not the
    same kind of thing. The model's value stands, and the provider judges it.

    A provider publishing a scalar where the model sends an object is the
    network disagreeing with itself; a select must not raise over it.
    """

    resource_attributes = build_resource_attributes(
        subject_category="Market",
        capability=_capability(advertised={"commodityGroup": "Vegetables"}),
        turn=_turn(location=None),
        model_filled={"commodityGroup": {"code": "VEG"}},
        schema_context_index=_SCHEMA_CONTEXT_INDEX,
        filterable=("commodityGroup",),
    )

    assert resource_attributes["commodityGroup"] == {"code": "VEG"}


def test_a_mixed_advertised_list_skips_what_it_cannot_match() -> None:
    """`coverageAreas` is the shape that forces this: its items are either an
    area reference or a bare GeoJSON geometry, and a provider may send a list
    holding values of neither shape.

    A selector cannot match a non-object, so those items are passed over rather
    than raising — this is network data of unknown shape, and a select must not
    fail on a field it was only echoing.
    """

    resource_attributes = build_resource_attributes(
        subject_category="Market",
        capability=_capability(
            advertised={
                "supportedCommodities": [
                    "Onion",
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


def test_narrowing_an_advertised_object_merges_into_it() -> None:
    """The model names one part of an object, not a replacement for it.

    It wrote `{"market": {"marketCode": ..., "state": ...}}` — the two fields
    `profile.json` offers — and the whole advertised object went with it,
    losing `marketName`, `district` and the market's own coordinates. The
    resource *is* Akluj APMC; a select says which commodity and when.
    """

    resource_attributes = build_resource_attributes(
        subject_category="Market",
        capability=_capability(
            advertised={
                "market": {
                    "marketName": "Akluj APMC",
                    "marketCode": "1806",
                    "district": "Sholapur",
                    "state": "Maharashtra",
                }
            }
        ),
        turn=_turn(location=None),
        # "Sholapur" is the district, written into a field holding the
        # market's code — a real model wrote exactly this. The advertised value
        # wins: an advertised field is a fact about the resource, the model's
        # is a guess, and the resource is Akluj APMC either way.
        model_filled={"market": {"marketCode": "Sholapur"}},
        schema_context_index=_SCHEMA_CONTEXT_INDEX,
        filterable=("market.marketCode", "market.state"),
    )

    assert resource_attributes["market"] == {
        "marketName": "Akluj APMC",
        "marketCode": "1806",
        "district": "Sholapur",
        "state": "Maharashtra",
    }


def test_the_turns_geometry_is_not_sent_to_a_pack_without_a_location() -> None:
    """Seven of the eight packs declare no top-level `location`. MandiPrice
    names only `market.location.geo` — the market's own coordinates.

    The fallback added one regardless, so a mandi select carried an undeclared
    field that read as a duplicate of `market.location`, and a pack declaring
    `additionalProperties: false` would refuse the call for it. The farmer's
    coordinates already reach the network as `/discover`'s spatial filter.
    """

    resource_attributes = build_resource_attributes(
        subject_category="Market",
        capability=_capability(advertised={"market": {"marketCode": "1806"}}),
        turn=_turn(location=Location(geometry=Geometry(coordinates=[74.06, 18.57]))),
        model_filled={},
        schema_context_index=_SCHEMA_CONTEXT_INDEX,
        # MandiPrice's own paths: a market location, and no top-level one.
        filterable=("market.marketCode", "market.location.geo"),
        declared=("market",),
    )

    assert "location" not in resource_attributes


def test_the_turns_geometry_reaches_a_pack_that_declares_a_location() -> None:
    """A facility search is "what is near *here*", and the point is the query.

    `AgricultureFacility` declares `location` but does not list it in
    `filterable_paths` — it is the search origin, not a filter over advertised
    values — and its own resource says to "invoke this Resource with a
    fulfillment stop carrying a Point location". Gating on the filterable list
    dropped it, and the provider had nothing to search around.
    """

    resource_attributes = build_resource_attributes(
        subject_category="Market",
        capability=_capability(
            advertised={"supportedFacilityTypes": ["KrishiVigyanKendra"]}
        ),
        turn=_turn(
            location=Location(geometry=Geometry(coordinates=[73.7898, 19.9975]))
        ),
        model_filled={"facilityType": "KrishiVigyanKendra"},
        schema_context_index=_SCHEMA_CONTEXT_INDEX,
        filterable=("supportedFacilityTypes", "facilityType"),
        # The pack declares `location` as a property without listing it as a
        # filter, which is how the flattened fields report it.
        declared=("supportedFacilityTypes", "facilityType", "location"),
    )

    assert resource_attributes["location"] == {
        "geo": {"type": "Point", "coordinates": [73.7898, 19.9975]}
    }


def test_the_turns_location_wins_over_the_models() -> None:
    """The model wrote `{"geo": "Nashik"}` — a place name where the schema
    requires a GeoJSON geometry — and the provider refused the call.

    The turn already carried the point, resolved from the farmer's own words by
    the district lookup. The model has no way to turn a name into coordinates,
    so its value here can only be worse than the one it replaced.
    """

    resource_attributes = build_resource_attributes(
        subject_category="Market",
        capability=_capability(),
        turn=_turn(
            location=Location(geometry=Geometry(coordinates=[73.7898, 19.9975]))
        ),
        model_filled={"location": {"geo": "Nashik"}},
        schema_context_index=_SCHEMA_CONTEXT_INDEX,
        filterable=("location.geo", "supportedParameters"),
        declared=("location", "supportedParameters"),
    )

    assert resource_attributes["location"] == {
        "geo": {"type": "Point", "coordinates": [73.7898, 19.9975]}
    }


def test_the_subject_category_comes_from_the_ask_not_the_advertisement() -> None:
    """A resource advertises what it serves; the ask says what was wanted.

    Echoing `subjectCategories` back from the discover response stated the
    provider's own advertisement as the question's category — so a scheme ask
    discovered by category alone (ADR-0009) selected a resource advertising
    `Crop` as a crop question.
    """

    capability = ProviderCapability(
        provider_id="kvk",
        provider_name="KVK",
        capability="openagrinet:MandiPrice",
        resource_id="res:kvk:advisory",
        observed_categories=("Crop", "Practice"),
    )

    resource_attributes = build_resource_attributes(
        capability=capability,
        subject_category="Scheme",
        turn=_turn(location=None),
        model_filled={},
        schema_context_index=_SCHEMA_CONTEXT_INDEX,
        filterable=_MANDI_FILTERABLE,
    )

    assert resource_attributes["subjectCategories"] == ["Scheme"]
