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
    paths = []
    for key, value in data.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            paths.extend(_flatten(value, prefix=f"{path}."))
        else:
            paths.append(path)
    return paths


def validate_arguments(resource_attributes: dict, schema: DomainSchema) -> None:
    """Raise ``InvalidArgument`` if any key path in ``resource_attributes`` is
    not one of the schema's ``filterable`` paths."""

    for path in _flatten(resource_attributes):
        if path not in schema.filterable:
            raise InvalidArgument(
                f"{path!r} is not a filterable field of {schema.type} "
                f"(filterable: {schema.filterable})"
            )
