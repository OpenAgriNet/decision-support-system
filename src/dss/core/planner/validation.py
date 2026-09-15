"""Validates the model's ``resource_attributes`` against a pack.

Three checks, each one a select the provider rejected:

- the name must be one the pack declares as filterable;
- a field the pack types as an array must get a list, not a single value;
- a field with a fixed set of values must get one of them, not the
  farmer's own words.

No "required minimum" check — ``profile.json`` has no ``required_filters``
key (design doc Open #3, unresolved network-wide); that stays open, not
improvised here.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

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
    # The type of each field, read from the pack's attributes.yaml. Empty when
    # the pack's fields could not be read. Then only the name check runs, as
    # before — a pack we cannot read the types of is not a fatal pack.
    field_types: Mapping[str, str] = {}
    # The allowed values of each field that has a fixed set. Most fields have
    # none, so a missing entry means "anything goes", not "nothing allowed".
    field_enums: Mapping[str, tuple[str, ...]] = {}


def parse_domain_schema(pack: SchemaPackFiles) -> DomainSchema:
    """Read ``filterable_paths`` from a pack's ``profile.json``, with the
    ``beckn:resourceAttributes.`` prefix stripped — the model's
    ``resource_attributes`` dict is unprefixed."""

    profile = json.loads(pack.profile_json)
    filterable = tuple(
        path.removeprefix(_BECKN_RESOURCE_ATTRIBUTES_PREFIX)
        for path in profile["filterable_paths"]
    )
    return DomainSchema(
        type=pack.pack_name,
        filterable=filterable,
        # Already resolved by the reading layer, which merged the pack's own
        # fields with the shared file's. Nothing is re-read here.
        field_types={path: spec.type for path, spec in pack.flattened_fields.items()},
        field_enums={
            path: spec.enum for path, spec in pack.flattened_fields.items() if spec.enum
        },
    )


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
        # `[]` only where the pack writes it: on a list *of objects*, whose
        # inner fields it spells `supportedCommodities[].code`. Without the
        # marker those flattened to `supportedCommodities.code`, which matches
        # no filterable path — the correct nested shape was rejected, and the
        # model learned to send the path string itself as a key instead.
        #
        # A list of plain values keeps the bare path (`topics`), which is what
        # the pack writes for a free-text list and what `or [path]` returns.
        nested = [
            nested_path
            for item in value
            for nested_path in _flatten_value(
                item, path=f"{path}[]" if isinstance(item, dict) else path
            )
        ]
        # a list of plain values flattens to the list's own path
        return nested or [path]
    return [path]


def _nest_example(key: str) -> dict:
    """The shape a path-shaped key should have been, for the retry message."""

    head, _, tail = key.partition(".")
    if not tail:
        return {key: "..."}
    if head.endswith("[]"):
        return {head[:-2]: [_nest_example(tail)]}
    return {head: _nest_example(tail)}


def _check_is_array(path: str, value: object, schema: DomainSchema) -> None:
    """Reject a scalar where the pack asks for a list.

    Only this one direction is checked. "It is a list" is a clear rule. The
    other types are not: reading them right means following each `$ref`, and
    a wrong guess rejects a body the provider would have taken.
    """

    if not schema.field_types.get(path, "").startswith("array"):
        return
    if isinstance(value, list):
        return
    raise InvalidArgument(
        f"{path!r} of {schema.type} is {schema.field_types[path]}, "
        f"so it takes a list — got {value!r}. Wrap it: [{value!r}]"
    )


def _check_is_allowed(path: str, value: object, schema: DomainSchema) -> None:
    """Reject a value the pack does not list.

    Checks each item of a list, because an array's enum sits on its items —
    ``supportedFacilityTypes`` is the four facility types, not four lists.

    A field with no enum is skipped: most fields are free text, so a missing
    entry means anything goes.
    """

    allowed = schema.field_enums.get(path)
    if not allowed:
        return
    given = value if isinstance(value, list) else [value]
    for item in given:
        if item in allowed:
            continue
        raise InvalidArgument(
            f"{item!r} is not a value {path!r} of {schema.type} takes. "
            f"Use one of: {', '.join(allowed)}"
        )


def validate_arguments(resource_attributes: dict, schema: DomainSchema) -> None:
    """Raise ``InvalidArgument`` if a key path in ``resource_attributes`` is
    not one of the schema's ``filterable`` paths, holds a scalar where the
    pack asks for a list, or holds a value the pack does not list."""

    # A key is a field name, never a path. `{"market.marketName": "Pune"}` is
    # the model copying the prompt's notation instead of nesting, and it
    # arrives beside the real `market` object rather than narrowing it.
    for key in resource_attributes:
        if "." in key or "[]" in key:
            raise InvalidArgument(
                f"{key!r} is a path, not a field name. Send the nested "
                f"structure instead: "
                f"{json.dumps(_nest_example(key))}"
            )

    for path in _flatten(resource_attributes):
        if path not in schema.filterable:
            raise InvalidArgument(
                f"{path!r} is not a filterable field of {schema.type} "
                f"(filterable: {schema.filterable})"
            )

    # Top level only. `field_types` is keyed by the pack's own paths, and a
    # nested path's type is not read here.
    for field, value in resource_attributes.items():
        _check_is_array(field, value, schema)
        _check_is_allowed(field, value, schema)
