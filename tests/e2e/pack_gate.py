"""The select gate every e2e fake network shares.

Each fake already refuses the one malformed select its own capability hit:
a place name where a geometry belongs, a missing search origin, the wrong
commodity. Those are hand-written, so each catches only the defect someone
already found — and five select defects reached production anyway.

This gate is not hand-written. It validates the body against the pack's own
`attributes.yaml`, which is what the provider validates against, so a fake
refuses a malformed select whether or not anyone anticipated that particular
malformation.

Used by all three e2e fakes ahead of their own checks, so a new capability
added later inherits the gate by using the same helper.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from werkzeug import Response

from tests.support.pack_schema import validate_resource_attributes


def resource_attributes(select_body: dict[str, Any]) -> dict[str, Any]:
    """The `resourceAttributes` a select carries, at the one path they live."""

    return select_body["message"]["contract"]["commitments"][0]["resources"][0][
        "resourceAttributes"
    ]


def pack_rejection(
    select_body: dict[str, Any], *, root: Path, pack_name: str
) -> Response | None:
    """A 400 mirroring the provider's NACK, or None if the pack accepts the body.

    400 rather than 404 or 422: `classify_status_code` treats only 400/401/403
    as a defect, so anything else is retried three times before failing — slow,
    and wrong about whose fault it is.

    The error names the failing paths, because a select that is refused without
    saying why is what made these defects take an afternoon each.
    """

    result = validate_resource_attributes(
        resource_attributes(select_body), root=root, pack_name=pack_name
    )
    if result.ok:
        return None
    return Response(
        json.dumps(
            {
                "error": "SCH_SCHEMA_VALIDATION_FAILED",
                "details": list(result.errors),
                "received": resource_attributes(select_body),
            }
        ),
        status=400,
        content_type="application/json",
    )
