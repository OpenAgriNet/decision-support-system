"""Fills in a recorded body's dates.

A recorded example carries the date it was recorded on —
`point-forecast.json` ends `2026-08-24T09:00:00Z` — so serving it verbatim
would answer a weather question with a forecast for a date already past, and a
price question with last month's arrival date.

The response files hold placeholders where a live provider would put real
dates, and this substitutes them: `{{now}}` in a `validity` window, `{{today}}`
for `arrivalDate`. Placeholders rather than the mock adding the fields unasked,
for two reasons: the files stay honest about which dates are part of the body,
and a scenario wanting a *deliberately* expired window stays writable.

Worth knowing where this does and does not bite. `_apply_expiry_filter` runs
inside `discover_providers` and applies only to `answers` — Direct resources
found at discovery. An answer returned from `/select` reaches
`assemble_evidence` without passing it. So under the OnDemand scenarios this
substitution is about not lying to whoever reads the answer, not about
avoiding a drop. It starts mattering the moment a Direct scenario is added.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

# What a response file writes where a live provider would put a timestamp.
NOW = "{{now}}"

# And where it would put a plain date. `arrivalDate` is typed as a date, not a
# timestamp — a mandi price is reported for a day — so filling it with a full
# timestamp would answer "the price this week" to the microsecond.
TODAY = "{{today}}"

# How wide the filled-in window is. A forecast's window is hours, not minutes,
# and it has to still cover `now` by the time a turn finishes.
_BEFORE = timedelta(hours=1)
_AFTER = timedelta(hours=12)


def fill_in_dates(
    attributes: dict[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    """A copy of `attributes` with its date placeholders replaced.

    `{{now}}` inside `validity` becomes a window around this moment;
    `arrivalDate`'s `{{today}}` becomes a plain date.

    Only the placeholders are substituted. Real dates are left alone, so a
    scenario can hold a fixed past window on purpose.

    The DSS parses these two keys as datetimes, and a window it cannot parse
    counts as no window — the field would look present and do nothing. So the
    output is `datetime.isoformat()`, which `fromisoformat` reads back.
    """

    moment = now or datetime.now(UTC)
    filled = dict(attributes)

    if filled.get("arrivalDate") == TODAY:
        filled["arrivalDate"] = moment.date().isoformat()

    window = attributes.get("validity")
    if isinstance(window, dict):
        replaced = dict(window)
        if replaced.get("startsAt") == NOW:
            replaced["startsAt"] = (moment - _BEFORE).isoformat()
        if replaced.get("endsAt") == NOW:
            replaced["endsAt"] = (moment + _AFTER).isoformat()
        if replaced != window:
            filled["validity"] = replaced

    if filled == attributes:
        return attributes
    return filled
