"""Reads what a pack says about itself: its kind, its @type, whether it serves
a place, and its fields.

A pack does not declare its fields in one place. It says "my fields are
whatever ``AgricultureResource`` has, plus mine" — an ``allOf`` with a
``$ref`` to another file. Knowing every field therefore means opening two
files and merging them, at every read. ``flatten_fields`` does that once, into
a map of field path to what the field is. ``profile.json`` stays the source of
truth for *which* fields matter (``filterable_paths``, ``result_fields``).

Pure: file text in, plain values out. The adapter reads the files, because
only it has paths.
"""

from __future__ import annotations

import json
from collections.abc import Collection

import yaml

from dss.core.schema_packs.models import FieldSpec, SchemaPackFiles
from dss.core.shared.schema import follow_ref, resolve_schema

# Catalog and API packs share one folder; `profile.json` names the kind.
_API_PACK_MODEL = "provider-api"
# The path segment that says a pack indexes a point.
_LOCATION_SEGMENT = "location"

# The pack is read as data, so any shape defect surfaces as one of these.
PACK_DEFECTS = (KeyError, TypeError, ValueError, yaml.YAMLError)


def is_api_pack(pack: SchemaPackFiles) -> bool:
    """A profile with no ``semantic_model`` is a catalog pack.

    Raises one of ``PACK_DEFECTS`` when ``profile.json`` can't be read.
    """
    profile = json.loads(pack.profile_json)
    if not isinstance(profile, dict):
        raise TypeError(f"profile.json of pack {pack.pack_name} is not an object")
    return profile.get("semantic_model") == _API_PACK_MODEL


def pack_needs_place(pack: SchemaPackFiles) -> bool:
    """True when the pack indexes a location, its own (`location.geo`) or a
    nested one (`market.location.geo`). Such a pack serves a place, so an ask
    must name one to be served and its discover call is filtered by it."""

    profile = json.loads(pack.profile_json)
    return any(
        _LOCATION_SEGMENT in path.split(".")
        for path in profile.get("indexable_paths", ())
    )


def is_place_needed(
    pack_types: Collection[str], place_bound_types: Collection[str]
) -> bool:
    """True when every pack type serves a place. One placeless type is enough
    for False: a place filter drops every resource that has no coverage area.
    With no pack type, no pack asks for a place.

    Settlement and discovery both read this, so an ask is never asked for a
    place its search would not use."""

    return bool(pack_types) and all(
        pack_type in place_bound_types for pack_type in pack_types
    )


def extract_type_const(attributes_yaml: str, pack_name: str) -> str:
    """The pack's canonical ``@type``, read from ``x-jsonld``.

    A pack declares its type twice. ``x-jsonld."@type"`` is a plain scalar;
    ``properties["@type"]`` is a ``oneOf`` because the schema also permits an
    array containing the canonical type alongside provider-defined ones.

    This reads ``x-jsonld``: one scalar with nothing to unwrap, and
    ``@context`` sits beside it so both come from the same place. Reading
    ``properties["@type"]["const"]`` raised ``KeyError('const')`` on every
    real pack — the ``const`` is nested inside the ``oneOf``.

    Every reader of a pack's @type uses this and fails the same way — one bad
    pack skipped, not the whole boot.
    """

    return _x_jsonld(attributes_yaml, pack_name)["@type"]


def extract_schema_context(attributes_yaml: str, pack_name: str) -> str:
    """The ``@context`` URL the pack declares beside its ``@type``."""

    return _x_jsonld(attributes_yaml, pack_name)["@context"]


def _x_jsonld(attributes_yaml: str, pack_name: str) -> dict:
    parsed = yaml.safe_load(attributes_yaml)
    schema = parsed["components"]["schemas"][pack_name]
    x_jsonld = schema.get("x-jsonld")
    if not x_jsonld:
        raise ValueError(f"no x-jsonld block for pack {pack_name}")
    return x_jsonld


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
