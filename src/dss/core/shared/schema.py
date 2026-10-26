from __future__ import annotations

from typing import Any

# Marks a `$ref` as pointing into the shared file rather than the pack's own.
# Any non-empty prefix would do — `_follow` only checks whether one is there.
_SHARED_FILE = "shared"


def resolve_schema(schema: dict, *, home: dict, shared: dict | None) -> dict:
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
        merged = _deep_merge(merged, resolve_schema(member, home=source, shared=shared))
    return merged


def follow_ref(node: dict, *, home: dict, shared: dict | None) -> dict:
    """A node with its top-level ``$ref`` replaced. ``allOf`` is left as is."""

    resolved, _ = _follow(node, home=home, shared=shared)
    return resolved


def _follow(node: dict, *, home: dict, shared: dict | None) -> tuple[dict, dict]:
    """A node with its ``$ref`` replaced, plus the document it now lives in.

    Returning the document matters: the shared file's own ``#/...`` refs point
    inside *itself*, so resolving them against the pack that pointed here
    would look in the wrong file.

    An ``https://`` ref is not followed: resolving it would need the network.
    The node comes back as it is.
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

    # Rebase before merging: once merged, nothing records which file the
    # target came from. Marked on every hop out of the shared file, not only
    # cross-file hops, or a ref local to it resolves against the pack.
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
