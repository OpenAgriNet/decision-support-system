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

import yaml
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
    # JSON Schema's `format`, for the fields that declare one. `type` alone
    # reads the same for `arrivalDate` as for free text, and the model wrote
    # "this week" where the pack asks for an ISO date.
    field_formats: Mapping[str, str] = {}
    # The allowed values of each field that has a fixed set. Most fields have
    # none, so a missing entry means "anything goes", not "nothing allowed".
    field_enums: Mapping[str, tuple[str, ...]] = {}


def _items_requiring_siblings(attributes_yaml: str) -> frozenset[str]:
    """Array fields whose item schema has a ``required`` list.

    Naming one field of such an item is not a filter the pack can read: it
    requires the whole item. `WeatherObservation.parameters` requires
    `[parameter, values, unit]`, so asking for a rainfall total also asserts
    a unit and a measurement — data only the provider has.

    Most arrays of objects require nothing (`services`, `supportedCommodities`,
    `agricultureSubjects`), and those stay filterable: one field of an
    unconstrained item is a legal narrowing.
    """

    try:
        document = yaml.safe_load(attributes_yaml)
    except yaml.YAMLError:
        # A pack we cannot read is not a fatal pack, matching the reading
        # layer: every path stays offered, as before this check existed.
        return frozenset()

    found: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if (
                    isinstance(value, dict)
                    and value.get("type") == "array"
                    and isinstance(value.get("items"), dict)
                    and value["items"].get("required")
                ):
                    found.add(key)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(document)
    return frozenset(found)


def parse_domain_schema(pack: SchemaPackFiles) -> DomainSchema:
    """Read ``filterable_paths`` from a pack's ``profile.json``, with the
    ``beckn:resourceAttributes.`` prefix stripped — the model's
    ``resource_attributes`` dict is unprefixed.

    Paths under an array whose items carry their own ``required`` list are
    dropped: see ``_items_requiring_siblings``.
    """

    profile = json.loads(pack.profile_json)
    answer_only = _items_requiring_siblings(pack.attributes_yaml)
    filterable = tuple(
        path
        for path in (
            raw.removeprefix(_BECKN_RESOURCE_ATTRIBUTES_PREFIX)
            for raw in profile["filterable_paths"]
        )
        if path.split("[]")[0] not in answer_only
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
        field_formats={
            path: spec.format
            for path, spec in pack.flattened_fields.items()
            if spec.format
        },
    )


def _flatten(data: dict, prefix: str = "", stop_at: tuple[str, ...] = ()) -> list[str]:
    """Every leaf path in ``data``, descending through dicts and lists.

    Lists matter: a list of objects (``{"topics": [{"evil": "x"}]}``) hides
    keys one level down, and recursing into dicts alone never saw them — so
    the model could smuggle arbitrary structure past validation and out to a
    provider. A list of plain values contributes only its own path, which is
    the normal case for a free-text field.
    """

    paths: list[str] = []
    for key, value in data.items():
        path = f"{prefix}{key}"
        # A *nested* filterable path names a value the caller supplies, and
        # what is inside that value is the value's own schema rather than more
        # fields the pack enumerates. `location.geo` is a GeoJSON geometry:
        # descending past it produced `location.geo.type`, which matches
        # nothing, so *valid* GeoJSON was rejected — and the shape that did
        # pass, the place name as a bare string, was what the provider refused.
        #
        # Nested only. A bare name like `topics` is a whole field, and a pack
        # describing it as free text does not thereby allow arbitrary objects
        # inside it — descending is what catches structure smuggled through a
        # list of them.
        if ("." in path or "[]" in path) and path in stop_at:
            paths.append(path)
            continue
        paths.extend(_flatten_value(value, path=path, stop_at=stop_at))
    # a list of plain values yields its own path once per item; dedupe so the
    # error message names a field once
    return list(dict.fromkeys(paths))


def _flatten_value(
    value: object, *, path: str, stop_at: tuple[str, ...] = ()
) -> list[str]:
    if isinstance(value, dict):
        return _flatten(value, prefix=f"{path}.", stop_at=stop_at)
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
                item,
                path=f"{path}[]" if isinstance(item, dict) else path,
                stop_at=stop_at,
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


def _check_is_object(path: str, value: object, schema: DomainSchema) -> None:
    """Reject a scalar where the pack asks for an object.

    The mirror of the array check, and for the same reason: the model wrote
    the farmer's word straight through — `{"location": "Nashik"}` where the
    pack types `location` an object — and only the provider noticed.
    """

    if schema.field_types.get(path) != "object":
        return
    if isinstance(value, dict):
        return
    raise InvalidArgument(
        f"{path!r} of {schema.type} is an object, not a value — got "
        f"{value!r}. Send the fields the pack names under it."
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
        if item is None:
            # `_value_at` yields one value per item of a list, so an item that
            # simply lacks the field arrives as `None`. Naming `None` as the
            # bad value describes something the model never wrote; what it has
            # to do is put the field on every item.
            raise InvalidArgument(
                f"every item of {path.split('[]')[0]!r} of {schema.type} needs "
                f"{path.rsplit('.', 1)[-1]!r}. Use one of: {', '.join(allowed)}"
            )
        raise InvalidArgument(
            f"{item!r} is not a value {path!r} of {schema.type} takes. "
            f"Use one of: {', '.join(allowed)}"
        )


def _value_at(data: object, path: str) -> object:
    """The value ``_flatten`` found at ``path``.

    Walks the same notation ``_flatten`` writes: ``.`` descends into an
    object, ``[]`` marks a list whose items are descended into. A ``[]``
    segment yields the values of every item, flattened, because the enum
    applies to each — `supportedCommodities[].code` is one code per item, and
    checking only the first would let a later bad one through.

    Returns ``None`` for a path that is not present. `_flatten` produced the
    path from this same data, so that means a list of plain values whose
    own path stands in for its items, and those are checked as the list.
    """

    if not path:
        return data
    head, _, tail = path.partition(".")
    if head.endswith("[]"):
        container = data.get(head[:-2]) if isinstance(data, dict) else None
        if not isinstance(container, list):
            return None
        return [_value_at(item, tail) for item in container]
    if not isinstance(data, dict) or head not in data:
        return None
    return _value_at(data[head], tail) if tail else data[head]


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

    # Shape before name. `{"location": "Nashik"}` flattens to `location`,
    # which is not a filterable path — so the name check would call a real
    # field unknown, when what is wrong is its shape. A misleading retry is
    # what taught the model to write the place name in the first place.
    for field, value in resource_attributes.items():
        _check_is_array(field, value, schema)
        _check_is_object(field, value, schema)

    for path in _flatten(resource_attributes, stop_at=schema.filterable):
        if path not in schema.filterable:
            raise InvalidArgument(
                f"{path!r} is not a filterable field of {schema.type} "
                f"(filterable: {schema.filterable})"
            )

        # The value at *this* path. Reading the loop variables above instead
        # checked whichever field that loop ended on, so with more than one
        # field a bad value on any but the last reached the provider — and
        # every enum test sent a single-key dict, where the two coincide.
        _check_is_allowed(path, _value_at(resource_attributes, path), schema)
