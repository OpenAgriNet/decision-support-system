from __future__ import annotations

from collections.abc import Mapping

import yaml

from dss.core.forms.models import (
    ActionField,
    ActionSchema,
    FieldSource,
    FormContext,
    JsonScalar,
)
from dss.core.provider_discovery.models import SchemaPackFiles
from dss.core.shared.schema import resolve_schema

_ACTION_MAP = "x-oan-schema-by-action"
# A desk's catalog entry. Discovery reads it; nobody fills it in.
_CATALOG_ACTION = "catalog/publish"
_SCHEME_PATH = "scheme.code"
_PROVIDER_PREFIX = "provider."


def parse_api_pack(pack: SchemaPackFiles) -> tuple[ActionSchema, ...]:
    """One ActionSchema per leaf, for every action but ``catalog/publish``.

    Raises ``ValueError``, ``KeyError``, ``TypeError`` or ``yaml.YAMLError``
    on a pack it cannot read — the same defects ``PACK_DEFECTS`` names.
    """

    try:
        return _read_pack(yaml.safe_load(pack.attributes_yaml))
    except AttributeError as exc:
        # A list where a mapping belongs fails on `.get` / `.items`. Callers
        # skip a bad pack on PACK_DEFECTS, which does not name AttributeError.
        raise TypeError(f"malformed API pack {pack.pack_name}: {exc}") from exc


def _read_pack(document: dict) -> tuple[ActionSchema, ...]:
    schemas = document["components"]["schemas"]
    category = _find_category_name(schemas)
    return tuple(
        schema
        for action, per_call_name in _served_actions(schemas[category]).items()
        for schema in _read_action(
            schemas[per_call_name], action=action, category=category, document=document
        )
    )


def _find_category_name(schemas: Mapping[str, dict]) -> str:
    roots = [name for name, schema in schemas.items() if _ACTION_MAP in schema]
    if len(roots) != 1:
        raise ValueError(f"expected one schema with {_ACTION_MAP}, found {len(roots)}")
    return roots[0]


def _served_actions(root: dict) -> dict[str, str]:
    return {
        action: per_call_name
        for action, per_call_name in root[_ACTION_MAP].items()
        if action != _CATALOG_ACTION
    }


def _read_action(
    per_call: dict, *, action: str, category: str, document: dict
) -> tuple[ActionSchema, ...]:
    return tuple(
        _read_leaf(leaf_ref, action=action, category=category, document=document)
        for leaf_ref in per_call["oneOf"]
    )


def _read_leaf(
    leaf_ref: dict, *, action: str, category: str, document: dict
) -> ActionSchema:
    leaf = _resolve(leaf_ref, document)
    must_send = tuple(
        _read_field(path, definition)
        for path in _must_send_paths(leaf)
        if (definition := _definition_at(leaf, path, document)) is not None
    )
    context = FormContext(
        scheme=_scheme_of(leaf, document), category=category, action=action
    )
    return ActionSchema(context=context, must_send=must_send)


def _must_send_paths(leaf: dict) -> list[str]:
    paths = leaf["x-oan-fields"]["mustSend"]
    if not isinstance(paths, list):
        raise TypeError(f"mustSend must be a list, got {type(paths).__name__}")
    return paths


def _definition_at(schema: dict, path: str, document: dict) -> dict | None:
    """The resolved schema at a dotted path, or ``None`` if the leaf does not
    declare it — a mustSend path only counts where properties declare it."""

    node = schema
    for segment in path.split("."):
        child = node.get("properties", {}).get(segment)
        if not isinstance(child, dict):
            return None
        node = _resolve(child, document)
    return node


def _scheme_of(leaf: dict, document: dict) -> str:
    definition = _definition_at(leaf, _SCHEME_PATH, document) or {}
    allowed = _allowed_values(definition)
    if len(allowed) != 1 or not isinstance(allowed[0], str):
        raise ValueError(f"leaf pins no single {_SCHEME_PATH}: {allowed}")
    return allowed[0]


def _read_field(path: str, definition: dict) -> ActionField:
    allowed = _allowed_values(definition)
    return ActionField(
        path=path,
        source=_source_of(path, allowed),
        allowed_values=allowed,
        pattern=definition.get("pattern"),
        min_length=definition.get("minLength"),
        max_length=definition.get("maxLength"),
        pii_class=definition.get("x-oan-pii", {}).get("class"),
    )


def _allowed_values(definition: dict) -> tuple[JsonScalar, ...]:
    if "enum" in definition:
        return tuple(definition["enum"])
    if "const" in definition:
        return (definition["const"],)
    return ()


def _source_of(path: str, allowed_values: tuple[JsonScalar, ...]) -> FieldSource:
    if len(allowed_values) == 1:
        return FieldSource.FIXED
    if path.startswith(_PROVIDER_PREFIX):
        return FieldSource.PROVIDER
    return FieldSource.FARMER


def _resolve(node: dict, document: dict) -> dict:
    # An API pack never points into another file, so there is no shared one.
    return resolve_schema(node, home=document, shared=None)
