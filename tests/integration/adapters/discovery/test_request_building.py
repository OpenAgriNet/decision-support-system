"""Contract tests for building /discover requests.

No business logic — no assertions on which @type was chosen, only that a
given ProviderQuery translates into the right wire shape. textSearch is
omitted: the network doesn't support it yet (plan-level decision), even
though the reference sample includes it.
"""

from __future__ import annotations

from dss.adapters.discovery.client import build_discover_request
from dss.core.provider_discovery.models import Coverage, ProviderQuery

SCHEMA_CONTEXT_INDEX = {
    "openagrinet:WeatherObservation": (
        "https://schemas.openagrinet.global/schema/WeatherObservation/v0.1/context.jsonld"
    )
}
SCHEMA_BASE_URL = "https://schemas.openagrinet.global/schema"


def test_builds_the_envelope_from_given_ids_and_timestamp() -> None:
    query = ProviderQuery(
        capabilities=("openagrinet:WeatherObservation",),
        subject_category="Weather",
        languages=("hi",),
        coverage=None,
    )

    request = build_discover_request(
        query,
        schema_context_index=SCHEMA_CONTEXT_INDEX,
        message_id="1c0a55d7-8e64-4b19-9a2f-33b7c6e1d905",
        transaction_id="9f2c1a8e-4b70-4d31-9c55-6f2e0b1d7a44",
        timestamp="2026-08-26T06:11:58.004Z",
    )

    assert request["context"]["action"] == "discover"
    assert request["context"]["version"] == "2.0.0"
    assert request["context"]["messageId"] == "1c0a55d7-8e64-4b19-9a2f-33b7c6e1d905"
    assert request["context"]["transactionId"] == "9f2c1a8e-4b70-4d31-9c55-6f2e0b1d7a44"
    assert request["context"]["timestamp"] == "2026-08-26T06:11:58.004Z"


def test_schema_context_is_built_from_the_capability_index() -> None:
    query = ProviderQuery(
        capabilities=("openagrinet:WeatherObservation",),
        subject_category="Weather",
        languages=("hi",),
        coverage=None,
    )

    request = build_discover_request(
        query,
        schema_context_index=SCHEMA_CONTEXT_INDEX,
        message_id="m",
        transaction_id="t",
        timestamp="2026-08-26T06:11:58.004Z",
    )

    assert request["context"]["schemaContext"] == [
        "https://schemas.openagrinet.global/schema/WeatherObservation/v0.1/"
        "context.jsonld#openagrinet:WeatherObservation"
    ]


def test_the_jsonpath_filter_matches_the_subject_category() -> None:
    """The filter matches `subjectCategories`, not `@type` — `schemaContext` is
    what names the resolved type (see the test above), so repeating it here
    would only exclude providers that omit `@type` on a resource."""

    query = ProviderQuery(
        capabilities=("openagrinet:WeatherObservation",),
        subject_category="Weather",
        languages=("hi",),
        coverage=None,
    )

    request = build_discover_request(
        query,
        schema_context_index=SCHEMA_CONTEXT_INDEX,
        message_id="m",
        transaction_id="t",
        timestamp="2026-08-26T06:11:58.004Z",
    )

    filters = request["message"]["intent"]["filters"]
    assert filters["type"] == "jsonpath"
    assert filters["expression"] == (
        "$.catalogs[*].resources[*] ? "
        '(@.resourceAttributes.subjectCategories[*] == "Weather")'
    )


def test_no_coverage_means_no_spatial_filter() -> None:
    query = ProviderQuery(
        capabilities=("openagrinet:WeatherObservation",),
        subject_category="Weather",
        languages=("hi",),
        coverage=None,
    )

    request = build_discover_request(
        query,
        schema_context_index=SCHEMA_CONTEXT_INDEX,
        message_id="m",
        transaction_id="t",
        timestamp="2026-08-26T06:11:58.004Z",
    )

    assert "spatial" not in request["message"]["intent"]


def test_coverage_becomes_an_s_dwithin_spatial_filter() -> None:
    query = ProviderQuery(
        capabilities=("openagrinet:WeatherObservation",),
        subject_category="Weather",
        languages=("hi",),
        coverage=Coverage(lat=19.9975, lon=73.7898, radius_m=250000),
    )

    request = build_discover_request(
        query,
        schema_context_index=SCHEMA_CONTEXT_INDEX,
        message_id="m",
        transaction_id="t",
        timestamp="2026-08-26T06:11:58.004Z",
    )

    spatial = request["message"]["intent"]["spatial"][0]
    assert spatial["op"] == "S_DWITHIN"
    assert spatial["geometry"] == {"type": "Point", "coordinates": [73.7898, 19.9975]}
    assert spatial["distanceMeters"] == 250000
    assert spatial["srid"] == "EPSG:4326"


def test_textsearch_is_never_included() -> None:
    query = ProviderQuery(
        capabilities=("openagrinet:WeatherObservation",),
        subject_category="Weather",
        languages=("hi",),
        coverage=None,
    )

    request = build_discover_request(
        query,
        schema_context_index=SCHEMA_CONTEXT_INDEX,
        message_id="m",
        transaction_id="t",
        timestamp="2026-08-26T06:11:58.004Z",
    )

    assert "textSearch" not in request["message"]["intent"]
