"""One flat list of a schema pack's fields.

A pack does not declare its fields in one place. It says "my fields are
whatever ``AgricultureResource`` has, plus mine" — an ``allOf`` with a
``$ref`` to another file. Knowing every field therefore means opening two
files and merging them, at every read.

This does that merge once, producing a map of field path to what the field
is. Two callers need it:

- ``/discover`` builds a filter over a field's allowed values.
- ``/select`` builds a body, so it needs field names and types.

``profile.json`` stays the source of truth for *which* fields matter
(``filterable_paths``, ``result_fields``). This says *what they are*.

Pure: yaml text in, plain dict out. The adapter reads the files, because only
it has paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml

# Marks a `$ref` as pointing into the shared file rather than the pack's own.
# Any non-empty prefix would do — `_follow` only checks whether one is there.
_SHARED_FILE = "shared"


@dataclass(frozen=True)
class FieldSpec:
    """What one field is, merged across everything that declares it."""

    type: str
    required: bool
    enum: tuple[str, ...] = field(default=())


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
    merged = _merge_all_of(schema, home=document, shared=shared)
    return _fields_of(merged, home=document, shared=shared)


def _merge_all_of(schema: dict, *, home: dict, shared: dict | None) -> dict:
    """Dissolve ``allOf`` into its parent, following any ``$ref`` first.

    The members are alternative declarations of the same object, so the
    result is one object carrying all of their properties. ``required`` is a
    union: a field required by any member is required.
    """

    resolved, source = _follow(schema, home=home, shared=shared)
    merged: dict[str, Any] = {
        key: value for key, value in resolved.items() if key != "allOf"
    }
    for member in resolved.get("allOf", ()):
        if not isinstance(member, dict):
            continue
        merged = _deep_merge(merged, _merge_all_of(member, home=source, shared=shared))
    return merged


def _follow(node: dict, *, home: dict, shared: dict | None) -> tuple[dict, dict]:
    """A node with its ``$ref`` replaced, plus the document it now lives in.

    Returning the document matters: the shared file's own ``#/...`` refs point
    inside *itself*, so resolving them against the pack that pointed here
    would look in the wrong file.

    An ``https://`` ref — the Beckn ones — is deliberately not followed.
    Resolving it would make flattening need the network, and the DSS builds
    those fields (``location``, ``descriptor``) from the turn rather than from
    a schema. It is left as a plain object.
    """

    ref = node.get("$ref")
    if not isinstance(ref, str) or ref.startswith("http"):
        return node, home

    file_part, _, pointer = ref.partition("#")
    source = home if not file_part else shared
    if source is None:
        return node, home

    target = source
    for segment in pointer.split("/"):
        if segment:
            target = target[segment]

    # Rebase before merging: the target's own `#/...` refs point inside the
    # file it came from, but once merged it sits beside fields from the pack's
    # file and nothing records where it came from.
    #
    # Marked whenever the target came out of the shared file — not only when
    # this hop crossed files. The chain is two hops deep: MandiPrice refs
    # `AgricultureResource`, which is itself an allOf refing
    # `AgricultureResourceFields`, whose `agricultureSubjects.items` refs
    # `AgricultureSubjectReference`. That third ref is local to the shared
    # file, so a "did this hop cross files" test misses it and the lookup
    # lands in the pack's document — `KeyError`.
    if source is shared:
        target = _mark_shared_refs(target)

    # Keys beside the $ref override what it points at.
    siblings = {key: value for key, value in node.items() if key != "$ref"}
    return _deep_merge(target, siblings), source


def _mark_shared_refs(node: Any) -> Any:
    """Rewrite a node's own `#/...` refs so they name the shared file.

    `#/components/schemas/X` becomes `shared#/components/schemas/X`, which
    `_follow` then resolves against the shared document rather than the
    pack's. Mirrors the upstream generator's own `rebase_internal_refs`.
    """

    if isinstance(node, list):
        return [_mark_shared_refs(item) for item in node]
    if not isinstance(node, dict):
        return node
    return {
        key: (
            f"{_SHARED_FILE}{value}"
            if key == "$ref" and isinstance(value, str) and value.startswith("#")
            else _mark_shared_refs(value)
        )
        for key, value in node.items()
    }


def _deep_merge(left: dict, right: dict) -> dict:
    """Right wins, except that ``required`` unions and dicts recurse."""

    result = dict(left)
    for key, value in right.items():
        if key not in result:
            result[key] = value
        elif isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        elif key == "required":
            result[key] = list(dict.fromkeys([*result[key], *value]))
        else:
            result[key] = value
    return result


def _fields_of(
    schema: dict, *, home: dict, shared: dict | None
) -> dict[str, FieldSpec]:
    required = set(schema.get("required", ()))
    fields: dict[str, FieldSpec] = {}
    for name, raw in schema.get("properties", {}).items():
        if not isinstance(raw, dict):
            continue
        # `_merge_all_of`, not `_follow`: a field is often written as
        # `allOf: [$ref: X]` so it can carry its own description beside the
        # ref. `_follow` sees no top-level `$ref` there and returns the
        # wrapper untouched, which types the field `object` with no enum.
        definition = _merge_all_of(raw, home=home, shared=shared)
        fields[name] = FieldSpec(
            type=_type_of(definition, home=home, shared=shared),
            required=name in required,
            enum=_enum_of(definition, home=home, shared=shared),
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
        items, _ = _follow(definition.get("items") or {}, home=home, shared=shared)
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
        items, _ = _follow(definition.get("items") or {}, home=home, shared=shared)
        return tuple(items.get("enum", ())) if items else ()
    return ()
