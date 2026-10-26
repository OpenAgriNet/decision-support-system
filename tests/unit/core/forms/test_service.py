from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from dss.core.forms.models import ActionField, ActionSchema, FieldSource, FormContext
from dss.core.forms.service import parse_api_pack
from dss.core.provider_discovery.models import SchemaPackFiles

# The Grievance pack at network-specs `api-schema-packs-v0.1`, untrimmed.
_GRIEVANCE_YAML = (
    Path(__file__).parent / "fixtures" / "grievance_attributes.yaml"
).read_text(encoding="utf-8")

_PMFBY_SUPPORT = FormContext(scheme="PMFBY", category="Grievance", action="support")
_PMFBY_STATUS = FormContext(scheme="PMFBY", category="Grievance", action="status")


def _pack(attributes_yaml: str) -> SchemaPackFiles:
    return SchemaPackFiles(
        pack_name="Grievance",
        version="v0.1",
        profile_json="{}",
        attributes_yaml=attributes_yaml,
        examples_json=(),
    )


def _schema_for(context: FormContext) -> ActionSchema:
    schemas = parse_api_pack(_pack(_GRIEVANCE_YAML))
    return next(schema for schema in schemas if schema.context == context)


def _field(schema: ActionSchema, path: str) -> ActionField:
    return next(field for field in schema.must_send if field.path == path)


def test_every_action_but_catalog_publish_becomes_one_schema_per_scheme() -> None:
    contexts = {schema.context for schema in parse_api_pack(_pack(_GRIEVANCE_YAML))}

    assert contexts == {
        _PMFBY_SUPPORT,
        _PMFBY_STATUS,
        FormContext(scheme="PM-KISAN", category="Grievance", action="init"),
        FormContext(scheme="PM-KISAN", category="Grievance", action="status"),
        FormContext(scheme="PM-KISAN", category="Grievance", action="support"),
    }


def test_must_send_keeps_the_leaf_order() -> None:
    schema = _schema_for(_PMFBY_SUPPORT)

    assert [field.path for field in schema.must_send] == [
        "informationMode",
        "scheme.code",
        "provider.id",
        "provider.descriptor.name",
        "enrolmentId",
        "applicantMobile",
        "cropYear",
        "season",
        "grievance.description",
    ]


def test_each_path_is_labelled_with_who_fills_it() -> None:
    schema = _schema_for(_PMFBY_SUPPORT)

    sources = {field.path: field.source for field in schema.must_send}

    assert sources == {
        "informationMode": FieldSource.FIXED,
        "scheme.code": FieldSource.FIXED,
        "provider.id": FieldSource.PROVIDER,
        "provider.descriptor.name": FieldSource.PROVIDER,
        "enrolmentId": FieldSource.FARMER,
        "applicantMobile": FieldSource.FARMER,
        "cropYear": FieldSource.FARMER,
        "season": FieldSource.FARMER,
        "grievance.description": FieldSource.FARMER,
    }


def test_a_fixed_path_carries_its_one_value() -> None:
    schema = _schema_for(_PMFBY_SUPPORT)

    assert _field(schema, "informationMode").allowed_values == ("OnDemand",)
    assert _field(schema, "scheme.code").allowed_values == ("PMFBY",)


def test_a_field_carries_the_rules_the_pack_writes_on_it() -> None:
    schema = _schema_for(_PMFBY_SUPPORT)

    assert _field(schema, "applicantMobile") == ActionField(
        path="applicantMobile",
        source=FieldSource.FARMER,
        pattern="^[6-9][0-9]{9}$",
        pii_class="contact",
    )
    assert _field(schema, "season").allowed_values == ("Kharif", "Rabi", "Zaid")


def test_a_ref_is_followed_to_the_rules_it_points_at() -> None:
    """`grievance.description` is `$ref: Description`; its limits live there."""
    description = _field(_schema_for(_PMFBY_SUPPORT), "grievance.description")

    assert (description.min_length, description.max_length) == (10, 2000)
    assert description.pii_class == "freetext"


def test_status_needs_the_mobile_and_the_ticket() -> None:
    schema = _schema_for(_PMFBY_STATUS)

    farmer_paths = [
        field.path for field in schema.must_send if field.source is FieldSource.FARMER
    ]

    assert farmer_paths == ["applicantMobile", "case.ticketNo"]


def test_a_must_send_path_the_leaf_does_not_declare_is_dropped() -> None:
    attributes = yaml.safe_load(_GRIEVANCE_YAML)
    leaf = attributes["components"]["schemas"]["PMFBYStatus"]
    leaf["x-oan-fields"]["mustSend"].append("case.notDeclared")

    schemas = parse_api_pack(_pack(yaml.safe_dump(attributes)))
    status = next(schema for schema in schemas if schema.context == _PMFBY_STATUS)

    assert "case.notDeclared" not in [field.path for field in status.must_send]


def test_a_pack_with_no_action_map_is_refused() -> None:
    attributes = yaml.safe_load(_GRIEVANCE_YAML)
    del attributes["components"]["schemas"]["Grievance"]["x-oan-schema-by-action"]

    with pytest.raises(ValueError, match="x-oan-schema-by-action"):
        parse_api_pack(_pack(yaml.safe_dump(attributes)))


def test_a_scheme_pinned_only_by_const_is_read() -> None:
    """`IsPMFBY` pins `scheme.code` with `const`; the leaf's own enum is optional."""
    attributes = yaml.safe_load(_GRIEVANCE_YAML)
    leaf = attributes["components"]["schemas"]["PMFBYStatus"]
    leaf["allOf"][1]["properties"]["scheme"]["properties"]["code"] = {"type": "string"}

    contexts = {
        schema.context for schema in parse_api_pack(_pack(yaml.safe_dump(attributes)))
    }

    assert _PMFBY_STATUS in contexts


def test_a_leaf_that_pins_no_scheme_is_refused() -> None:
    attributes = yaml.safe_load(_GRIEVANCE_YAML)
    schemas = attributes["components"]["schemas"]
    schemas["IsPMFBY"]["properties"]["scheme"]["properties"]["code"] = {
        "type": "string"
    }
    schemas["PMFBYStatus"]["allOf"][1]["properties"]["scheme"]["properties"]["code"] = {
        "type": "string"
    }

    with pytest.raises(ValueError, match="scheme"):
        parse_api_pack(_pack(yaml.safe_dump(attributes)))


def test_unreadable_yaml_is_refused() -> None:
    with pytest.raises(yaml.YAMLError):
        parse_api_pack(_pack("components: [unclosed"))


def test_a_fixed_value_keeps_its_json_type() -> None:
    """Task 3 sends FIXED values as they are; `4` must not become `"4"`."""
    attributes = yaml.safe_load(_GRIEVANCE_YAML)
    leaf = attributes["components"]["schemas"]["PMFBYStatus"]
    leaf["allOf"][1]["properties"]["attempt"] = {"type": "integer", "enum": [4]}
    leaf["x-oan-fields"]["mustSend"].append("attempt")

    schemas = parse_api_pack(_pack(yaml.safe_dump(attributes)))
    status = next(schema for schema in schemas if schema.context == _PMFBY_STATUS)

    assert _field(status, "attempt").allowed_values == (4,)


@pytest.mark.parametrize(
    "attributes_yaml",
    [
        "components: {schemas: []}",
        "components: {schemas: {Root: {x-oan-schema-by-action: [support]}}}",
    ],
)
def test_a_list_where_a_mapping_belongs_is_a_type_error(attributes_yaml: str) -> None:
    with pytest.raises(TypeError):
        parse_api_pack(_pack(attributes_yaml))


def test_a_must_send_that_is_not_a_list_is_refused() -> None:
    attributes = yaml.safe_load(_GRIEVANCE_YAML)
    leaf = attributes["components"]["schemas"]["PMFBYStatus"]
    leaf["x-oan-fields"]["mustSend"] = "applicantMobile"

    with pytest.raises(TypeError, match="mustSend"):
        parse_api_pack(_pack(yaml.safe_dump(attributes)))
