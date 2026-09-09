"""Fills in a recorded body's validity window.

A recorded example carries the date it was recorded on —
`point-forecast.json` ends `2026-08-24T09:00:00Z` — so serving it verbatim
would answer a weather question with a forecast for a date already past.

The response files hold `"{{now}}"` where a live provider would put a real
timestamp, and this substitutes it. A placeholder rather than the mock adding
the field unasked, for two reasons: the file stays honest about `validity`
being part of the body, and a scenario wanting a *deliberately* expired window
stays writable.

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

# How wide the filled-in window is. A forecast's window is hours, not minutes,
# and it has to still cover `now` by the time a turn finishes.
_BEFORE = timedelta(hours=1)
_AFTER = timedelta(hours=12)


def fill_in_validity(
    attributes: dict[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    """A copy of `attributes` with any `{{now}}` in `validity` replaced.

    Only the placeholder is substituted. A window with real dates is left
    alone, so a scenario can hold a fixed past window on purpose.

    The DSS parses these two keys as datetimes, and a window it cannot parse
    counts as no window — the field would look present and do nothing. So the
    output is `datetime.isoformat()`, which `fromisoformat` reads back.
    """

    window = attributes.get("validity")
    if not isinstance(window, dict):
        return attributes

    moment = now or datetime.now(UTC)
    filled = dict(window)
    if filled.get("startsAt") == NOW:
        filled["startsAt"] = (moment - _BEFORE).isoformat()
    if filled.get("endsAt") == NOW:
        filled["endsAt"] = (moment + _AFTER).isoformat()

    if filled == window:
        return attributes
    return {**attributes, "validity": filled}
