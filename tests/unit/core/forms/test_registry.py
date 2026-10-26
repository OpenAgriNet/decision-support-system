from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from dss.core.forms.models import FormContext, FormDefinition
from dss.core.forms.registry import FormLookup, FormRegistry
from dss.core.forms.service import build_form_registry
from dss.core.provider_discovery.models import SchemaPackFiles, SchemaPackSkipped

_GRIEVANCE_YAML = (
    Path(__file__).parent / "fixtures" / "grievance_attributes.yaml"
).read_text(encoding="utf-8")

_PMFBY_SUPPORT = FormContext(scheme="PMFBY", category="Grievance", action="support")
_PMFBY_STATUS = FormContext(scheme="PMFBY", category="Grievance", action="status")


def _pack(attributes_yaml: str, *, pack_name: str = "Grievance") -> SchemaPackFiles:
    return SchemaPackFiles(
        pack_name=pack_name,
        version="v0.1",
        profile_json="{}",
        attributes_yaml=attributes_yaml,
        examples_json=(),
    )


def test_registry_finds_a_form_by_its_context() -> None:
    form = FormDefinition(context=_PMFBY_SUPPORT, fields=())

    registry = FormRegistry([form])

    assert registry.find(_PMFBY_SUPPORT) == form
    assert registry.find(_PMFBY_STATUS) is None


def test_an_empty_registry_finds_nothing() -> None:
    assert FormRegistry().find(_PMFBY_SUPPORT) is None
    assert FormRegistry().skipped == ()


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
    form = FormDefinition(context=_PMFBY_SUPPORT, fields=())

    with pytest.raises(ValueError, match="PMFBY/Grievance/support"):
        FormRegistry([form, form])


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
