from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from dss.core.forms.models import ActionSchema, FormContext, FormDefinition
from dss.core.forms.registry import FormLookup, FormRegistry
from dss.core.forms.service import build_form_registry
from dss.core.provider_discovery.models import SchemaPackFiles, SchemaPackSkipped

_GRIEVANCE_YAML = (
    Path(__file__).parents[3] / "fixtures" / "api_packs" / "grievance_attributes.yaml"
).read_text(encoding="utf-8")

_PMFBY_SUPPORT = FormContext(scheme="PMFBY", category="Grievance", action="support")
_PMFBY_STATUS = FormContext(scheme="PMFBY", category="Grievance", action="status")
_API_PROFILE = '{"semantic_model": "provider-api"}'


def _pack(
    attributes_yaml: str,
    *,
    pack_name: str = "Grievance",
    profile_json: str = _API_PROFILE,
) -> SchemaPackFiles:
    return SchemaPackFiles(
        pack_name=pack_name,
        version="v0.1",
        profile_json=profile_json,
        attributes_yaml=attributes_yaml,
        examples_json=(),
    )


def test_registry_finds_a_form_by_its_context() -> None:
    schema = ActionSchema(context=_PMFBY_SUPPORT, must_send=())

    registry = FormRegistry([schema])

    assert registry.find(_PMFBY_SUPPORT) == FormDefinition(
        context=_PMFBY_SUPPORT, fields=()
    )
    assert registry.find(_PMFBY_STATUS) is None


def test_an_empty_registry_finds_nothing() -> None:
    assert FormRegistry().find(_PMFBY_SUPPORT) is None
    assert FormRegistry().skipped == ()


def test_registry_counts_its_forms() -> None:
    schema = ActionSchema(context=_PMFBY_SUPPORT, must_send=())

    assert len(FormRegistry([schema])) == 1
    assert len(FormRegistry()) == 0


def test_registry_satisfies_the_lookup_the_orchestrator_takes() -> None:
    lookup: FormLookup = FormRegistry()

    assert lookup.find(_PMFBY_SUPPORT) is None


def test_the_grievance_pack_serves_pmfby_filing_and_status() -> None:
    registry = build_form_registry([_pack(_GRIEVANCE_YAML)])

    support = registry.find(_PMFBY_SUPPORT)

    assert support is not None
    assert [field.name for field in support.fields] == [
        "enrolmentId",
        "applicantMobile",
        "cropYear",
        "season",
        "grievance.description",
    ]
    assert registry.find(_PMFBY_STATUS) is not None


def test_a_scheme_that_needs_an_init_step_is_left_out_and_named() -> None:
    """PM-KISAN files only after the OTP that `init` starts, which the DSS lacks."""
    registry = build_form_registry([_pack(_GRIEVANCE_YAML)])

    for action in ("init", "status", "support"):
        kisan = FormContext(scheme="PM-KISAN", category="Grievance", action=action)
        assert registry.find(kisan) is None
    assert {skip.pack_name for skip in registry.skipped} == {"Grievance"}
    assert len(registry.skipped) == 3


def test_an_init_in_one_pack_rules_out_the_scheme_in_another() -> None:
    """The rule is per scheme, not per file: `init` and `support` may ship apart."""
    attributes = yaml.safe_load(_GRIEVANCE_YAML)
    schemas = attributes["components"]["schemas"]
    schemas["Grievance"]["x-oan-schema-by-action"] = {"init": "GrievanceInit"}
    schemas["GrievanceInit"]["oneOf"] = [{"$ref": "#/components/schemas/PMFBYStatus"}]
    init_only = _pack(yaml.safe_dump(attributes), pack_name="PmfbyInit")

    registry = build_form_registry([init_only, _pack(_GRIEVANCE_YAML)])

    assert registry.find(_PMFBY_SUPPORT) is None


def test_two_forms_for_one_context_are_refused() -> None:
    schema = ActionSchema(context=_PMFBY_SUPPORT, must_send=())

    with pytest.raises(ValueError, match="PMFBY/Grievance/support"):
        FormRegistry([schema, schema])


@pytest.mark.parametrize(
    "broken_yaml",
    [
        pytest.param("components: [unclosed", id="unreadable yaml"),
        pytest.param("components: {schemas: []}", id="list for a mapping"),
        pytest.param(
            "components: {schemas: {Root: {x-oan-schema-by-action: {support: Call}},"
            " Call: {oneOf: [{$ref: '#/components/schemas/Leaf'}]},"
            " Leaf: {allOf: [{$ref: '#/components/schemas/Leaf'}]}}}",
            id="ref cycle",
        ),
    ],
)
def test_a_pack_that_cannot_be_read_is_skipped_not_raised(broken_yaml: str) -> None:
    registry = build_form_registry(
        [_pack(broken_yaml, pack_name="Broken"), _pack(_GRIEVANCE_YAML)]
    )

    assert registry.find(_PMFBY_SUPPORT) is not None
    assert "Broken" in {skip.pack_name for skip in registry.skipped}


def test_a_second_form_for_a_taken_context_is_skipped() -> None:
    attributes = yaml.safe_load(_GRIEVANCE_YAML)
    leaf = attributes["components"]["schemas"]["PMFBYSupport"]
    leaf["x-oan-fields"]["mustSend"].remove("cropYear")
    second = _pack(yaml.safe_dump(attributes), pack_name="GrievanceCopy")

    registry = build_form_registry([_pack(_GRIEVANCE_YAML), second])

    support = registry.find(_PMFBY_SUPPORT)
    assert support is not None
    assert support.field("cropYear") is not None
    assert (
        SchemaPackSkipped(
            "GrievanceCopy", "PMFBY/Grievance/support: a form for this context exists"
        )
        in registry.skipped
    )


def test_a_catalog_pack_beside_the_api_packs_is_not_a_form_source() -> None:
    """Both kinds share one folder; only `profile.json` tells them apart."""
    catalog = _pack(
        "components: {schemas: {MandiPrice: {}}}",
        pack_name="MandiPrice",
        profile_json='{"semantic_model": "generalised"}',
    )

    registry = build_form_registry([catalog, _pack(_GRIEVANCE_YAML)])

    assert registry.find(_PMFBY_SUPPORT) is not None
    assert "MandiPrice" not in {skip.pack_name for skip in registry.skipped}


@pytest.mark.parametrize(
    "profile_json",
    [
        pytest.param("{not json", id="unreadable profile"),
        pytest.param("[]", id="not an object"),
    ],
)
def test_a_pack_whose_kind_cannot_be_read_is_skipped(profile_json: str) -> None:
    unknown = _pack(_GRIEVANCE_YAML, pack_name="Unknown", profile_json=profile_json)

    registry = build_form_registry([unknown])

    assert registry.find(_PMFBY_SUPPORT) is None
    assert [skip.pack_name for skip in registry.skipped] == ["Unknown"]


def test_the_registry_keeps_the_schema_the_form_was_cut_from() -> None:
    registry = build_form_registry([_pack(_GRIEVANCE_YAML)])

    schema = registry.action_schema(_PMFBY_SUPPORT)

    assert schema is not None
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
    assert registry.action_schema(_PMFBY_STATUS) is not None
    assert FormRegistry().action_schema(_PMFBY_SUPPORT) is None
