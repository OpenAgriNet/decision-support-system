"""Checks a mock body against the pack that describes it.

The mock writes `resourceAttributes` by hand, so it can invent a field the
pack never declared. Nothing downstream would notice: the DSS copies
`resourceAttributes` through as an opaque dict, the composer renders whatever
is in it, and a local run then looks like proof of something the real network
would reject.

Field names, required-ness and enums — not full JSON Schema. The pack's refs
chain out to `schema.beckn.io` (`WeatherObservation` →
`AgricultureResourceFields` → `GeoJSONGeometry`), so validating a body
carrying `location` would need the network, and the DSS builds that field from
the turn rather than from a schema. This catches the mistakes a hand-written
body actually makes; it says nothing about whether `location` is valid GeoJSON.
"""

from __future__ import annotations

from typing import Any

from dss.core.provider_discovery.schema_fields import FieldSpec

# JSON-LD framing, in every body and in no pack's `properties`. Refusing it
# would refuse every real payload.
_FRAMING = ("@context",)


class InvalidMockBody(Exception):
    """A mock body disagrees with the pack. Raised at startup, not per request:
    a wrong fixture is a bug in the mock, and finding it when the file is read
    beats finding it mid-turn."""


def check_against_pack(
    attributes: dict[str, Any], *, fields: dict[str, FieldSpec]
) -> None:
    """Refuse a `resourceAttributes` the pack does not describe.

    Every problem is collected before raising, so one run names all of them
    rather than one per fix.
    """

    problems: list[str] = []
    problems.extend(_undeclared(attributes, fields))
    problems.extend(_missing_required(attributes, fields))
    problems.extend(_outside_enum(attributes, fields))

    if problems:
        raise InvalidMockBody(
            "the mock body disagrees with its schema pack: " + "; ".join(problems)
        )


def _undeclared(attributes: dict[str, Any], fields: dict[str, FieldSpec]) -> list[str]:
    return [
        f"{name!r} is not a field this pack declares"
        for name in attributes
        if name not in fields and name not in _FRAMING
    ]


def _missing_required(
    attributes: dict[str, Any], fields: dict[str, FieldSpec]
) -> list[str]:
    return [
        f"{name!r} is required and absent"
        for name, spec in fields.items()
        if spec.required and name not in attributes
    ]


def _outside_enum(
    attributes: dict[str, Any], fields: dict[str, FieldSpec]
) -> list[str]:
    """Values the pack's enum does not allow.

    An array is checked per item, because that is where the enum lives — the
    pack puts it on `items`, and `subjectCategories` is the field a discover
    filter matches on.
    """

    problems: list[str] = []
    for name, value in attributes.items():
        spec = fields.get(name)
        if spec is None or not spec.enum:
            continue
        values = value if isinstance(value, list) else [value]
        for item in values:
            if item not in spec.enum:
                problems.append(
                    f"{name!r} is {item!r}, which the pack does not allow "
                    f"({', '.join(spec.enum)})"
                )
    return problems
