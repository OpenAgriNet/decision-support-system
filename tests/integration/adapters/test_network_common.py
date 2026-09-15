"""Contract tests for wire-format parsing shared across network adapters."""

from __future__ import annotations

import pytest

from dss.adapters.network_common import (
    classify_status_code,
    extract_source_reference,
    extract_validity,
    to_utc,
)
from dss.core.provider_discovery.models import FailureClass


def test_to_utc_attaches_zone_to_a_naive_datetime() -> None:
    result = to_utc("2026-08-26T00:00:00", end_of_day=False)
    assert result.isoformat() == "2026-08-26T00:00:00+00:00"


def test_to_utc_stretches_a_bare_end_date_to_end_of_day() -> None:
    result = to_utc("2026-08-26", end_of_day=True)
    assert result.isoformat() == "2026-08-26T23:59:59.999999+00:00"


def test_to_utc_keeps_a_bare_start_date_at_midnight() -> None:
    result = to_utc("2026-08-26", end_of_day=False)
    assert result.isoformat() == "2026-08-26T00:00:00+00:00"


def test_extract_validity_returns_none_when_absent() -> None:
    assert extract_validity({}) is None


def test_extract_validity_parses_both_bounds() -> None:
    validity = extract_validity(
        {
            "validity": {
                "startsAt": "2026-08-26T00:00:00Z",
                "endsAt": "2026-08-26T23:59:59Z",
            }
        }
    )
    assert validity is not None
    assert validity.starts_at.isoformat() == "2026-08-26T00:00:00+00:00"
    assert validity.ends_at.isoformat() == "2026-08-26T23:59:59+00:00"


@pytest.mark.parametrize("status_code", [400, 401, 403])
def test_classify_status_code_treats_bad_request_family_as_defect(
    status_code: int,
) -> None:
    assert classify_status_code(status_code) == FailureClass.DEFECT


@pytest.mark.parametrize("status_code", [429, 500, 0])
def test_classify_status_code_treats_everything_else_as_transient(
    status_code: int,
) -> None:
    assert classify_status_code(status_code) == FailureClass.TRANSIENT


def test_extract_source_reference_returns_none_when_absent() -> None:
    """`source` is optional on every pack that declares it."""

    assert extract_source_reference({}) is None


def test_extract_source_reference_reads_all_three_fields() -> None:
    source = extract_source_reference(
        {
            "source": {
                "sourceId": "agmarknet-mock",
                "sourceName": "Agmarknet Vistaar",
                "sourceUri": "https://agmarknet.gov.in",
            }
        }
    )

    assert source == ("agmarknet-mock", "Agmarknet Vistaar", "https://agmarknet.gov.in")


def test_extract_source_reference_tolerates_the_optional_fields() -> None:
    """Only `sourceId` is required by SourceReference."""

    assert extract_source_reference({"source": {"sourceId": "mausamgram"}}) == (
        "mausamgram",
        None,
        None,
    )


@pytest.mark.parametrize(
    "uri",
    [
        "urn:openagrinet:source:agmarknet",
        "did:web:agmarknet.gov.in",
        "openagrinet:sourceId",
        "ftp://agmarknet.gov.in/prices",
        "",
    ],
)
def test_a_source_uri_a_farmer_cannot_open_is_dropped(uri: str) -> None:
    """`sourceUri` is typed `format: uri`, so it may be a JSON-LD identifier
    rather than a page. `Source.url` is farmer-visible, so only http(s) passes."""

    assert (
        extract_source_reference({"source": {"sourceId": "s", "sourceUri": uri}})[2]
        is None
    )
