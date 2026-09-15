"""Wire-format parsing shared by every network adapter (discover, select, ...).

Pure translation only — no business logic.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from dss.core.provider_discovery.models import FailureClass, Validity

_BARE_DATE_LENGTH = len("YYYY-MM-DD")
_FARMER_OPENABLE_SCHEMES = frozenset({"http", "https"})

# Our own bad request; every other status — 429, 500, and anything unlisted —
# is treated as transient/retry-worthy.
_DEFECT_STATUS_CODES = {
    httpx.codes.BAD_REQUEST,
    httpx.codes.UNAUTHORIZED,
    httpx.codes.FORBIDDEN,
}
NO_STATUS_CODE = 0  # a pure network-level failure never had an HTTP response


def classify_status_code(status_code: int) -> FailureClass:
    if status_code in _DEFECT_STATUS_CODES:
        return FailureClass.DEFECT
    return FailureClass.TRANSIENT


def to_utc(value: str, *, end_of_day: bool) -> datetime:
    """The spec allows a bare date (`2026-08-26`), which parses naive and at
    midnight. Core compares validity against a tz-aware now, so a zone has to
    be attached here. A bare endsAt means valid *through* that day, so it
    stretches to the day's end; a bare startsAt already means the day's start.
    """
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        return parsed
    if end_of_day and len(value) == _BARE_DATE_LENGTH:
        parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
    return parsed.replace(tzinfo=UTC)


def extract_validity(attributes: dict[str, Any]) -> Validity | None:
    validity = attributes.get("validity")
    if validity is None:
        return None
    starts_at = validity.get("startsAt")
    ends_at = validity.get("endsAt")
    return Validity(
        starts_at=to_utc(starts_at, end_of_day=False) if starts_at else None,
        ends_at=to_utc(ends_at, end_of_day=True) if ends_at else None,
    )


def extract_source_reference(
    attributes: dict[str, Any],
) -> tuple[str, str | None, str | None] | None:
    """Read `resourceAttributes.source` as `(id, name, url)`.

    - Names who authored the data, which is not always who served it.
    - `sourceUri` is typed `format: uri`, so it may be a JSON-LD identifier
      rather than a page; only http(s) reaches a farmer.
    """

    source = attributes.get("source")
    if not source:
        return None
    uri = source.get("sourceUri") or ""
    openable = urlsplit(uri).scheme in _FARMER_OPENABLE_SCHEMES
    return source["sourceId"], source.get("sourceName"), uri if openable else None
