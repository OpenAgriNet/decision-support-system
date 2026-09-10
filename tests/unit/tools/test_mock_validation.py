"""Tier 1 — the mock checks its own bodies against the pack.

A mock that serves a field the pack never declared is worse than no mock: the
DSS accepts it, the composer quotes it, and a local run looks like proof of
something the real network would reject.

Field names, required-ness and enums only — not full JSON Schema. The refs
chain out to `schema.beckn.io` (`WeatherObservation` →
`AgricultureResourceFields` → `GeoJSONGeometry`), so validating a body
carrying `location` would need the network. This catches what actually goes
wrong when a body is written by hand, and says nothing about whether
`location`'s inner shape is valid GeoJSON.
"""

from __future__ import annotations

import pytest

from dss.core.provider_discovery.schema_fields import FieldSpec
from tools.mock_network.validation import InvalidMockBody, check_against_pack

_FIELDS = {
    "@type": FieldSpec(type="choice", required=True),
    "informationMode": FieldSpec(
        type="string", required=True, enum=("OnDemand", "Direct")
    ),
    "subjectCategories": FieldSpec(
        type="array<string>", required=True, enum=("Weather", "Market")
    ),
    "observationType": FieldSpec(
        type="string", required=False, enum=("Observation", "Forecast")
    ),
    "parameters": FieldSpec(type="array<object>", required=False),
}


def test_a_body_declaring_only_pack_fields_passes() -> None:
    check_against_pack(
        {
            "@type": "openagrinet:WeatherObservation",
            "informationMode": "Direct",
            "subjectCategories": ["Weather"],
            "observationType": "Forecast",
            "parameters": [{"parameter": "Rainfall"}],
        },
        fields=_FIELDS,
    )


def test_a_field_the_pack_never_declared_is_refused() -> None:
    """A misspelled key sitting beside the real one.

    Named in the error, because "invalid body" without the field name means
    reading the pack by hand to find which of twenty keys is wrong.
    """

    with pytest.raises(InvalidMockBody, match="observaationType"):
        check_against_pack(
            {
                "@type": "openagrinet:WeatherObservation",
                "informationMode": "Direct",
                "subjectCategories": ["Weather"],
                "observaationType": "Forecast",
            },
            fields=_FIELDS,
        )


def test_a_missing_required_field_is_refused() -> None:
    """`informationMode` absent is the failure that costs the most.

    A resource without it is dropped by discovery without a word, so the turn
    comes back `no_match` and the mock looks like it answered.
    """

    with pytest.raises(InvalidMockBody, match="informationMode"):
        check_against_pack(
            {
                "@type": "openagrinet:WeatherObservation",
                "subjectCategories": ["Weather"],
            },
            fields=_FIELDS,
        )


def test_a_value_outside_the_packs_enum_is_refused() -> None:
    """An enum is a wire contract, not documentation.

    A provider answering `informationMode: "Live"` would be dropped as
    unrecognised, and the enum is exactly what the flattener reads the pack
    for.
    """

    with pytest.raises(InvalidMockBody, match="Live"):
        check_against_pack(
            {
                "@type": "openagrinet:WeatherObservation",
                "informationMode": "Live",
                "subjectCategories": ["Weather"],
            },
            fields=_FIELDS,
        )


def test_the_json_ld_context_is_allowed_though_no_pack_declares_it() -> None:
    """`@context` is in every body and in no pack's `properties`.

    It is JSON-LD framing rather than a field, so refusing it would refuse
    every real payload.
    """

    check_against_pack(
        {
            "@context": "https://example.test/WeatherObservation/v0.1/context.jsonld",
            "@type": "openagrinet:WeatherObservation",
            "informationMode": "Direct",
            "subjectCategories": ["Weather"],
        },
        fields=_FIELDS,
    )
