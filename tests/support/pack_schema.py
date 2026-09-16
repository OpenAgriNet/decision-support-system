"""Validate a select body against the schema pack the provider validates it with.

Every select defect in this area shared one shape: a test asserted what the
author expected the payload to look like, a fixture encoded the same expectation,
the two agreed with each other, and the provider refused the result. Five of
them shipped that way.

The only reader that cannot agree with our expectations is the pack itself. This
module validates a built `resourceAttributes` against the pack's own
`attributes.yaml` with `jsonschema`, so a wrong shape fails locally with the
error the provider would have sent.

**Remote refs are not resolved.** `AgricultureResource` points at four
`schema.beckn.io` schemas (`Location`, `Address`, `Descriptor`,
`GeoJSONGeometry`). Vendoring them would put copies with no staleness signal in
the repo — the same trap that already cost an afternoon when `commodity` was
removed upstream. Instead an unresolvable subschema is replaced by a permissive
one and reported in `unchecked`, so a caller can see what was *not* checked
rather than reading a pass as full coverage. `location.geo` keeps the dedicated
assertion it already has in the weather e2e test.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

_REMOTE = "https://schema.beckn.io/"

# What replaces a subschema whose `$ref` is remote: anything passes. A stricter
# placeholder would reject bodies the provider accepts, and a validator that
# rejects a correct shape is worse than no validator — it teaches the model a
# wrong one, which is how the `[]` and `location.geo` defects were created.
_ANYTHING: dict[str, Any] = {}


@dataclass(frozen=True)
class PackValidation:
    """The result of checking one body against one pack."""

    errors: tuple[str, ...]
    unchecked: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def _strip_remote_refs(node: Any, *, unchecked: set[str]) -> Any:
    """Replace every remote `$ref` with a permissive schema, recording it.

    Recursive because the refs sit several levels down, under `properties` and
    `allOf` members rather than at the top of a schema.
    """

    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith(_REMOTE):
            unchecked.add(ref)
            return dict(_ANYTHING)
        return {
            key: _strip_remote_refs(value, unchecked=unchecked)
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_strip_remote_refs(item, unchecked=unchecked) for item in node]
    return node


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _pack_dir(root: Path, pack_name: str) -> Path:
    """The single version directory of one pack, matching the real loader."""

    return next((root / pack_name).glob("v*"))


def resource_schema(root: Path, pack_name: str) -> tuple[dict, Registry, set[str]]:
    """The pack's own resource schema, its ref registry, and what went unchecked.

    The shared `AgricultureResource` file is registered under the *relative* URI
    the packs actually write (`../../AgricultureResource/v0.1/attributes.yaml`),
    because that is the string `jsonschema` will look up.
    """

    unchecked: set[str] = set()
    pack_dir = _pack_dir(root, pack_name)
    document = _strip_remote_refs(
        _load(pack_dir / "attributes.yaml"), unchecked=unchecked
    )

    shared_dir = _pack_dir(root, "AgricultureResource")
    shared = _strip_remote_refs(
        _load(shared_dir / "attributes.yaml"), unchecked=unchecked
    )
    relative = f"../../AgricultureResource/{shared_dir.name}/attributes.yaml"

    registry = Registry().with_resources(
        [
            (
                relative,
                Resource.from_contents(shared, default_specification=DRAFT202012),
            ),
            ("", Resource.from_contents(document, default_specification=DRAFT202012)),
        ]
    )

    schemas = document["components"]["schemas"]
    # The pack's own resource schema is the one named after the pack.
    schema = schemas.get(pack_name) or next(iter(schemas.values()))
    return schema, registry, unchecked


def validate_resource_attributes(
    attributes: dict[str, Any], *, root: Path, pack_name: str
) -> PackValidation:
    """Check one `resourceAttributes` body against its pack.

    Returns every error rather than raising on the first, so a caller sees the
    whole list instead of fixing one required field and meeting the next — the
    exact loop that made this class of defect recur.
    """

    schema, registry, unchecked = resource_schema(root, pack_name)
    validator = Draft202012Validator(schema, registry=registry)
    errors = tuple(
        f"{'.'.join(str(part) for part in error.absolute_path) or '<root>'}: "
        f"{error.message}"
        for error in sorted(validator.iter_errors(attributes), key=str)
    )
    return PackValidation(errors=errors, unchecked=tuple(sorted(unchecked)))
