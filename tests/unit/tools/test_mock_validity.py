"""Tier 1 — the mock's validity window covers now.

A recorded example carries the date it was recorded on
(`point-forecast.json` ends 2026-08-24T09:00:00Z), so serving it verbatim
would answer a weather question with a forecast for a date in the past. The
files keep a placeholder and the mock fills it in per response.

A placeholder rather than the mock adding the field unasked: the file stays
honest about `validity` being part of the body, and a scenario that wants a
*deliberately* expired window stays possible — which omitting the field makes
impossible to write.
"""

from __future__ import annotations

from datetime import UTC, datetime

from tools.mock_network.validity import NOW, TODAY, fill_in_dates

_NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


def test_the_placeholder_becomes_a_window_around_now() -> None:
    filled = fill_in_dates({"validity": {"startsAt": NOW, "endsAt": NOW}}, now=_NOW)

    window = filled["validity"]
    assert window["startsAt"] < "2026-09-09T12:00:00" < window["endsAt"]


def test_a_real_date_is_left_alone() -> None:
    """Only the placeholder is substituted.

    A scenario testing the expiry filter needs to keep a fixed past window, so
    substituting every date would take that away.
    """

    fixed = {"validity": {"startsAt": "2020-01-01T00:00:00Z", "endsAt": "2020-01-02"}}

    assert fill_in_dates(fixed, now=_NOW) == fixed


def test_a_body_with_no_validity_is_unchanged() -> None:
    """`validity` is optional in the pack, and most bodies have none."""

    body = {"@type": "openagrinet:WeatherObservation", "informationMode": "Direct"}

    assert fill_in_dates(body, now=_NOW) == body


def test_a_date_placeholder_becomes_todays_date() -> None:
    """`arrivalDate` is a date, not a timestamp.

    A mandi price is reported for a day, and the pack types the field as a
    plain date. Filling it with a full timestamp would answer "what is the
    price this week" with a value stamped to the microsecond.
    """

    filled = fill_in_dates({"arrivalDate": TODAY}, now=_NOW)

    assert filled["arrivalDate"] == "2026-09-09"


def test_the_window_is_what_the_dss_parses() -> None:
    """The DSS reads these two keys and parses them as datetimes.

    A window it cannot parse is treated as no window at all, so the field
    would look present and do nothing.
    """

    filled = fill_in_dates({"validity": {"startsAt": NOW, "endsAt": NOW}}, now=_NOW)

    window = filled["validity"]
    starts = datetime.fromisoformat(window["startsAt"])
    ends = datetime.fromisoformat(window["endsAt"])
    assert starts <= _NOW <= ends
