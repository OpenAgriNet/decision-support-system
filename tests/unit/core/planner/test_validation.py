"""Tier 1 — validating the model's resource_attributes against a pack.

Two checks: the field name must be one the pack declares as filterable, and
a field the pack types as an array must get a list.

No "required minimum" check here — profile.json has no required_filters key
(design doc Open #3, unresolved network-wide).
"""

from __future__ import annotations

import json

import pytest

from dss.core.planner.validation import (
    DomainSchema,
    InvalidArgument,
    parse_domain_schema,
    validate_arguments,
)
from dss.core.provider_discovery.models import SchemaPackFiles
from dss.core.provider_discovery.schema_fields import FieldSpec

_MANDI_PROFILE = json.dumps(
    {
        "included_schemas": ["MandiPrice"],
        "filterable_paths": [
            "beckn:resourceAttributes.commodity.code",
            "beckn:resourceAttributes.market.marketCode",
            "beckn:resourceAttributes.market.state",
        ],
    }
)


def _mandi_pack_files() -> SchemaPackFiles:
    return SchemaPackFiles(
        pack_name="MandiPrice",
        version="0.1",
        profile_json=_MANDI_PROFILE,
        attributes_yaml="",
        examples_json=(),
    )


def test_parse_domain_schema_strips_the_beckn_prefix() -> None:
    schema = parse_domain_schema(_mandi_pack_files())
    assert schema.type == "MandiPrice"
    assert schema.filterable == (
        "commodity.code",
        "market.marketCode",
        "market.state",
    )


def test_validate_arguments_accepts_a_filterable_path() -> None:
    schema = parse_domain_schema(_mandi_pack_files())
    validate_arguments({"commodity": {"code": "PADDY"}}, schema)  # must not raise


def test_validate_arguments_rejects_an_invented_field() -> None:
    schema = parse_domain_schema(_mandi_pack_files())
    with pytest.raises(InvalidArgument):
        validate_arguments({"cropVariety": "Basmati"}, schema)


def test_an_invented_field_inside_a_list_is_rejected() -> None:
    """``_flatten`` recursed into dicts only, so a key hidden inside a list
    of objects was never seen — the model could smuggle arbitrary structure
    into ``resourceAttributes`` and out to a provider, which is the one thing
    this module exists to prevent."""

    schema = DomainSchema(type="KnowledgeAdvisory", filterable=("topics",))

    # `topics[].evil`, not `topics.evil`: the `[]` is how a pack spells a path
    # inside a list of objects, and the flattened path has to use the same
    # notation or it matches nothing.
    with pytest.raises(InvalidArgument, match=r"topics\[\].evil"):
        validate_arguments({"topics": [{"evil": "x"}]}, schema)


def test_a_list_of_plain_values_is_still_accepted() -> None:
    """``topics`` legitimately holds free text, so a list of strings is the
    normal case and must not start failing."""

    schema = DomainSchema(type="KnowledgeAdvisory", filterable=("topics",))

    validate_arguments({"topics": ["soil advisory", "pest management"]}, schema)


def test_parse_domain_schema_carries_the_packs_field_types() -> None:
    """The types are already resolved on the pack. Without this they never
    reach the validator, and the array check has nothing to check against."""

    pack = SchemaPackFiles(
        pack_name="AgricultureFacility",
        version="0.1",
        profile_json=json.dumps(
            {
                "included_schemas": ["AgricultureFacility"],
                "filterable_paths": ["beckn:resourceAttributes.supportedFacilityTypes"],
            }
        ),
        attributes_yaml="",
        examples_json=(),
        flattened_fields={
            "supportedFacilityTypes": FieldSpec(type="array<string>", required=False)
        },
    )

    schema = parse_domain_schema(pack)

    assert schema.field_types == {"supportedFacilityTypes": "array<string>"}


def test_a_scalar_where_the_pack_declares_an_array_is_rejected() -> None:
    """The model wrote ``"KrishiVigyanKendra"`` for a field the pack declares
    as ``array<string>``, and the provider rejected the select. The name was
    right, so a name-only check passed it straight through to the network."""

    schema = DomainSchema(
        type="AgricultureFacility",
        filterable=("supportedFacilityTypes",),
        field_types={"supportedFacilityTypes": "array<string>"},
    )

    with pytest.raises(InvalidArgument, match="supportedFacilityTypes"):
        validate_arguments({"supportedFacilityTypes": "KrishiVigyanKendra"}, schema)


def test_a_list_for_an_array_field_is_accepted() -> None:
    """What a correct model sends. Without this the array check could reject
    every valid list and the suite would still pass."""

    schema = DomainSchema(
        type="AgricultureFacility",
        filterable=("supportedFacilityTypes",),
        field_types={"supportedFacilityTypes": "array<string>"},
    )

    validate_arguments({"supportedFacilityTypes": ["KrishiVigyanKendra"]}, schema)


def test_a_value_outside_the_packs_enum_is_rejected() -> None:
    """The model wrote the farmer's own words for a field with a fixed set of
    values, and the provider rejected the select. The name was right and the
    value was a string, so neither existing check saw it."""

    schema = DomainSchema(
        type="AgricultureFacility",
        filterable=("facilityType",),
        field_types={"facilityType": "string"},
        field_enums={"facilityType": ("KrishiVigyanKendra", "Warehouse")},
    )

    with pytest.raises(InvalidArgument, match="KrishiVigyanKendra"):
        validate_arguments({"facilityType": "krishi kendra"}, schema)


def test_an_allowed_value_passes_on_its_own_and_inside_a_list() -> None:
    """What a correct model sends. An array's enum sits on its items, so the
    check reads each item — without this it could reject every valid list."""

    schema = DomainSchema(
        type="AgricultureFacility",
        filterable=("facilityType", "supportedFacilityTypes"),
        field_types={"supportedFacilityTypes": "array<string>"},
        field_enums={
            "facilityType": ("KrishiVigyanKendra", "Warehouse"),
            "supportedFacilityTypes": ("KrishiVigyanKendra", "Warehouse"),
        },
    )

    validate_arguments({"facilityType": "KrishiVigyanKendra"}, schema)
    validate_arguments({"supportedFacilityTypes": ["Warehouse"]}, schema)


def test_a_bad_value_inside_a_list_is_rejected() -> None:
    """The enum of an array applies to its items, so one wrong entry among
    good ones must still fail."""

    schema = DomainSchema(
        type="AgricultureFacility",
        filterable=("supportedFacilityTypes",),
        field_types={"supportedFacilityTypes": "array<string>"},
        field_enums={"supportedFacilityTypes": ("KrishiVigyanKendra", "Warehouse")},
    )

    with pytest.raises(InvalidArgument, match="krishi kendra"):
        validate_arguments(
            {"supportedFacilityTypes": ["Warehouse", "krishi kendra"]}, schema
        )


def test_the_correct_nested_shape_for_a_list_field_is_accepted() -> None:
    """`profile.json` writes a list-of-objects path as
    `supportedCommodities[].code`, meaning "inside each item of that array".
    The model sends the real structure, and `_flatten` has to spell the path it
    walks the same way.

    It used to drop the marker and produce `supportedCommodities.code`, which
    matched nothing — so the correct shape was rejected, the model retried, and
    it learned to send the path string itself as a key. That passed validation
    and reached the provider as `{"supportedCommodities[].code": "23"}`.
    """

    schema = DomainSchema(
        type="MandiPrice",
        filterable=("supportedCommodities[].code", "market.marketName"),
    )

    validate_arguments({"supportedCommodities": [{"code": "23"}]}, schema)


@pytest.mark.parametrize(
    ("key", "value", "nested"),
    [
        ("supportedCommodities[].code", "23", '{"supportedCommodities": [{"code"'),
        ("market.marketName", "Pune", '{"market": {"marketName"'),
    ],
    ids=["inside-a-list", "inside-an-object"],
)
def test_a_filterable_path_used_as_a_key_is_rejected(
    key: str, value: str, nested: str
) -> None:
    """A key is a field name, never a path — the model copying the prompt's
    notation verbatim instead of nesting.

    Both spellings, because they take different branches: one carries `[]` and
    a dot, the other only a dot. One real select carried both at once, each
    sitting beside the object it was meant to narrow rather than narrowing it.

    The message names the shape it should have been, so the retry has
    something to copy rather than a rule to infer.
    """

    schema = DomainSchema(
        type="MandiPrice",
        filterable=("supportedCommodities[].code", "market.marketName"),
    )

    with pytest.raises(InvalidArgument) as raised:
        validate_arguments({key: value}, schema)

    assert nested in str(raised.value)
