"""Tier 1 — assembling resourceAttributes for a /select call.

Structural fields (@context, @type, subjectCategories, location) are built
here from discovery data and the turn; the model's own resource_attributes
(e.g. topics, a resolved commodity code) are merged on top. No network, no
framework.
"""

from __future__ import annotations

from dss.core.planner.resource_attributes import build_resource_attributes
from dss.core.provider_discovery.models import ProviderCapability
from dss.core.shared.models import Geometry, Location, UserTurn

_SCHEMA_CONTEXT_INDEX = {
    "openagrinet:MandiPrice": "https://schemas.openagrinet.global/schema/MandiPrice/v0.1/context.jsonld",
}
_SCHEMA_BASE_URL = "https://schemas.openagrinet.global/schema"


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
    )

    assert resource_attributes["commodity"] == {"code": "PADDY", "name": "Paddy"}


def test_model_filled_cannot_override_a_structural_field() -> None:
    resource_attributes = build_resource_attributes(
        capability=_capability(),
        turn=_turn(location=None),
        model_filled={"@type": "something-else"},
        schema_context_index=_SCHEMA_CONTEXT_INDEX,
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
    )

    assert resource_attributes["supportedCommodities"] == [
        {"code": "23", "name": "Onion"}
    ]
