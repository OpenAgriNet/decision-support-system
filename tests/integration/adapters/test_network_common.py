"""Contract tests for wire-format parsing shared across network adapters."""

from __future__ import annotations

from dss.adapters.network_common import extract_validity, to_utc


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
