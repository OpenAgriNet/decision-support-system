"""Tier 1 — validating the model's resource_attributes against a pack's
filterable paths (plan issue #10).

No "required minimum" check here — profile.json has no required_filters key
(design doc Open #3, unresolved network-wide). This only catches an invented
field the pack never declared as filterable.
"""

from __future__ import annotations

import json

import pytest

from dss.core.planner.validation import (
    InvalidArgument,
    parse_domain_schema,
    validate_arguments,
)
from dss.core.provider_discovery.models import SchemaPackFiles

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
