"""One flat list of a schema pack's fields.

A pack does not declare its fields in one place. It says "my fields are
whatever ``AgricultureResource`` has, plus mine" — an ``allOf`` with a
``$ref`` to another file. Knowing every field therefore means opening two
files and merging them, at every read.

This flattens the merged schema once, into a map of field path to what the
field is. Two callers need it:

- ``/discover`` builds a filter over a field's allowed values.
- ``/select`` builds a body, so it needs field names and types.

``profile.json`` stays the source of truth for *which* fields matter
(``filterable_paths``, ``result_fields``). This says *what they are*.

Pure: yaml text in, plain dict out. The adapter reads the files, because only
it has paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import yaml

from dss.core.shared.schema import follow_ref, resolve_schema


@dataclass(frozen=True)
class FieldSpec:
    """What one field is, merged across everything that declares it."""

    type: str
    required: bool
    enum: tuple[str, ...] = field(default=())
    # JSON Schema's `format`, when the pack declares one. `type` alone does not
    # say enough: `arrivalDate` is a `string` like 85 other fields, and
    # `format: date` is the half that says an ISO date rather than free text.
    format: str | None = None


def flatten_fields(
    attributes_yaml: str,
    *,
    pack_name: str,
    shared_yaml: str | None,
) -> dict[str, FieldSpec]:
    """Every field the pack declares, keyed by path.

    ``shared_yaml`` is ``AgricultureResource``'s own ``attributes.yaml`` —
    the one file every pack's cross-file ``$ref`` points at. ``None`` when the
    pack has no such ref.
    """

    document = yaml.safe_load(attributes_yaml)
    shared = yaml.safe_load(shared_yaml) if shared_yaml else None
    schema = document["components"]["schemas"][pack_name]
    merged = resolve_schema(schema, home=document, shared=shared)
    return _fields_of(merged, home=document, shared=shared)


def _fields_of(
    schema: dict, *, home: dict, shared: dict | None
) -> dict[str, FieldSpec]:
    required = set(schema.get("required", ()))
    fields: dict[str, FieldSpec] = {}
    for name, raw in schema.get("properties", {}).items():
        if not isinstance(raw, dict):
            continue
        # `resolve_schema`, not `follow_ref`: a field is often written as
        # `allOf: [$ref: X]` so it can carry its own description beside the
        # ref. `follow_ref` sees no top-level `$ref` there and returns the
        # wrapper untouched, which types the field `object` with no enum.
        definition = resolve_schema(raw, home=home, shared=shared)
        fields[name] = FieldSpec(
            type=_type_of(definition, home=home, shared=shared),
            required=name in required,
            enum=_enum_of(definition, home=home, shared=shared),
            format=definition.get("format"),
        )
    return fields


def _type_of(definition: dict, *, home: dict, shared: dict | None) -> str:
    """The field's type, as a caller would describe it.

    An array reports what it holds — ``array<string>`` — because a caller
    building a body needs to know it is a list of strings, not merely "an
    array". ``const`` and ``oneOf``/``anyOf`` have no ``type`` of their own.
    """

    declared = definition.get("type")
    if declared == "array":
        items = follow_ref(definition.get("items") or {}, home=home, shared=shared)
        inner = _type_of(items, home=home, shared=shared) if items else "object"
        return f"array<{inner}>"
    if declared:
        return declared
    if "const" in definition:
        return "constant"
    if "oneOf" in definition or "anyOf" in definition:
        return "choice"
    return "object"


def _enum_of(definition: dict, *, home: dict, shared: dict | None) -> tuple[str, ...]:
    """The field's allowed values.

    For an array, the enum sits on ``items``, not on the array — and it is the
    array's values a ``/discover`` filter matches against, so reading the
    array's own (absent) enum would silently give nothing to filter by.
    """

    if "enum" in definition:
        return tuple(definition["enum"])
    if definition.get("type") == "array":
        items = follow_ref(definition.get("items") or {}, home=home, shared=shared)
        return tuple(items.get("enum", ())) if items else ()
    return ()
