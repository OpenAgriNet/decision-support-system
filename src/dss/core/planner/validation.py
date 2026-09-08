"""Validates the model's ``resource_attributes`` against a pack's filterable
paths.

Only catches an invented field the pack never declared as filterable. No
"required minimum" check — ``profile.json`` has no ``required_filters`` key
(design doc Open #3, unresolved network-wide); that stays open, not
improvised here.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict

from dss.core.provider_discovery.models import SchemaPackFiles

_BECKN_RESOURCE_ATTRIBUTES_PREFIX = "beckn:resourceAttributes."


class InvalidArgument(Exception):
    """``resource_attributes`` used a field the pack does not declare as
    filterable. The tool raises Pydantic AI's ``ModelRetry`` on this."""


class DomainSchema(BaseModel):
    """What the planner needs from a schema pack to validate a `select` call."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str
    filterable: tuple[str, ...]


def parse_domain_schema(pack: SchemaPackFiles) -> DomainSchema:
    """Read ``filterable_paths`` from a pack's ``profile.json``, with the
    ``beckn:resourceAttributes.`` prefix stripped — the model's
    ``resource_attributes`` dict is unprefixed."""

    profile = json.loads(pack.profile_json)
    filterable = tuple(
        path.removeprefix(_BECKN_RESOURCE_ATTRIBUTES_PREFIX)
        for path in profile["filterable_paths"]
    )
    return DomainSchema(type=pack.pack_name, filterable=filterable)


def _flatten(data: dict, prefix: str = "") -> list[str]:
    """Every leaf path in ``data``, descending through dicts and lists.

    Lists matter: a list of objects (``{"topics": [{"evil": "x"}]}``) hides
    keys one level down, and recursing into dicts alone never saw them — so
    the model could smuggle arbitrary structure past validation and out to a
    provider. A list of plain values contributes only its own path, which is
    the normal case for a free-text field.
    """

    paths: list[str] = []
    for key, value in data.items():
        paths.extend(_flatten_value(value, path=f"{prefix}{key}"))
    # a list of plain values yields its own path once per item; dedupe so the
    # error message names a field once
    return list(dict.fromkeys(paths))


def _flatten_value(value: object, *, path: str) -> list[str]:
    if isinstance(value, dict):
        return _flatten(value, prefix=f"{path}.")
    if isinstance(value, list):
        nested = [
            nested_path
            for item in value
            for nested_path in _flatten_value(item, path=path)
        ]
        # a list of plain values flattens to the list's own path
        return nested or [path]
    return [path]


def validate_arguments(resource_attributes: dict, schema: DomainSchema) -> None:
    """Raise ``InvalidArgument`` if any key path in ``resource_attributes`` is
    not one of the schema's ``filterable`` paths."""

    for path in _flatten(resource_attributes):
        if path not in schema.filterable:
            raise InvalidArgument(
                f"{path!r} is not a filterable field of {schema.type} "
                f"(filterable: {schema.filterable})"
            )
