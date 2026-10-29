from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest
import yaml

from dss.config.clarification_text_loader import load_clarification_text
from dss.core.forms.models import (
    ActionField,
    ActionSchema,
    FieldError,
    FieldErrorCode,
    FieldErrorEntry,
    FieldSource,
    FieldValue,
    FormContext,
    FormDefinition,
    FormErrorBlock,
    FormErrors,
    FormSubmission,
    PiiClass,
    ValidSubmission,
)
from dss.core.forms.registry import FormRegistry
from dss.core.forms.service import (
    WITHHELD,
    build_action_attributes,
    build_form_registry,
    candidates_for_scheme,
    form_error_block,
    free_text_of,
    offer_form,
    parse_api_pack,
    validate_submission,
    withhold_values,
    without_farmer_values,
)
from dss.core.provider_discovery.models import ProviderCapability, SchemaPackFiles
from tests.support.providers import grievance_provider

# The Grievance pack, untrimmed, as network-specs first published it. It lives on
# `schema-packs-v0.1` now; its URLs here still say `api-schemas/`.
_GRIEVANCE_YAML = (
    Path(__file__).parents[3] / "fixtures" / "api_packs" / "grievance_attributes.yaml"
).read_text(encoding="utf-8")

_PMFBY_SUPPORT = FormContext(scheme="PMFBY", category="Grievance", action="support")
_PMFBY_STATUS = FormContext(scheme="PMFBY", category="Grievance", action="status")


def _pack(attributes_yaml: str, *, profile_json: str = "{}") -> SchemaPackFiles:
    return SchemaPackFiles(
        pack_name="Grievance",
        version="v0.1",
        profile_json=profile_json,
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


def test_each_offer_gets_its_own_id() -> None:
    form = FormDefinition(context=_PMFBY_SUPPORT, fields=())

    first, second = offer_form(form), offer_form(form)

    assert first.form == second.form == form
    assert first.form_id and first.form_id != second.form_id


_PMFBY_VALUES = {
    "enrolmentId": "PMFBY-2024-000123",
    "applicantMobile": "9876543210",
    "cropYear": "2024",
    "season": "Kharif",
    "grievance.description": "Claim for the Kharif crop loss has not been paid.",
}


def _submission(values: Mapping[str, str], *, context: FormContext = _PMFBY_SUPPORT):
    return FormSubmission(
        form_id="offer-1",
        context=context,
        submission_id="submission-1",
        values=tuple(
            FieldValue(name=name, value=value) for name, value in values.items()
        ),
    )


def _forms() -> FormRegistry:
    api_pack = _pack(_GRIEVANCE_YAML, profile_json='{"semantic_model": "provider-api"}')
    return build_form_registry([api_pack])


def test_a_phone_off_the_pattern_is_the_only_error() -> None:
    submission = _submission({**_PMFBY_VALUES, "applicantMobile": "12345"})

    outcome = validate_submission(submission, _forms())

    assert isinstance(outcome, FormErrors)
    assert outcome.field_errors == (
        FieldError(name="applicantMobile", code=FieldErrorCode.PATTERN),
    )


def test_a_field_sent_twice_is_a_duplicate() -> None:
    submission = _submission(_PMFBY_VALUES)
    twice = submission.model_copy(
        update={"values": (*submission.values, FieldValue(name="season", value="Rabi"))}
    )

    outcome = validate_submission(twice, _forms())

    assert isinstance(outcome, FormErrors)
    assert outcome.field_errors == (
        FieldError(name="season", code=FieldErrorCode.DUPLICATE),
    )


def test_a_context_nobody_serves_is_an_unknown_form() -> None:
    kisan = FormContext(scheme="PM-KISAN", category="Grievance", action="support")

    outcome = validate_submission(_submission(_PMFBY_VALUES, context=kisan), _forms())

    assert outcome == FormErrors(
        form_id="offer-1",
        context=kisan,
        submission_id="submission-1",
        definition=None,
    )


def test_a_missing_required_field_is_reported_as_required() -> None:
    without_year = {k: v for k, v in _PMFBY_VALUES.items() if k != "cropYear"}

    outcome = validate_submission(_submission(without_year), _forms())

    assert isinstance(outcome, FormErrors)
    assert outcome.field_errors == (
        FieldError(name="cropYear", code=FieldErrorCode.REQUIRED),
    )


def test_a_value_under_the_minimum_length_is_too_short() -> None:
    submission = _submission({**_PMFBY_VALUES, "enrolmentId": "PMFBY-1"})

    outcome = validate_submission(submission, _forms())

    assert isinstance(outcome, FormErrors)
    assert outcome.field_errors == (
        FieldError(name="enrolmentId", code=FieldErrorCode.MIN_LENGTH),
    )


def test_a_value_over_the_maximum_length_is_too_long() -> None:
    submission = _submission({**_PMFBY_VALUES, "enrolmentId": "PMFBY-" + "9" * 30})

    outcome = validate_submission(submission, _forms())

    assert isinstance(outcome, FormErrors)
    assert outcome.field_errors == (
        FieldError(name="enrolmentId", code=FieldErrorCode.MAX_LENGTH),
    )


def test_a_value_outside_the_options_is_not_an_option() -> None:
    submission = _submission({**_PMFBY_VALUES, "season": "Monsoon"})

    outcome = validate_submission(submission, _forms())

    assert isinstance(outcome, FormErrors)
    assert outcome.field_errors == (
        FieldError(name="season", code=FieldErrorCode.NOT_AN_OPTION),
    )


def test_a_field_the_form_does_not_have_is_unknown() -> None:
    submission = _submission({**_PMFBY_VALUES, "village": "Anand"})

    outcome = validate_submission(submission, _forms())

    assert isinstance(outcome, FormErrors)
    assert outcome.field_errors == (
        FieldError(name="village", code=FieldErrorCode.UNKNOWN_FIELD),
    )


def test_an_error_block_names_each_bad_field_with_its_label_and_message() -> None:
    definition = _forms().find(_PMFBY_SUPPORT)
    assert definition is not None
    errors = FormErrors(
        form_id="offer-1",
        context=_PMFBY_SUPPORT,
        submission_id="submission-1",
        definition=definition,
        field_errors=(
            FieldError(name="applicantMobile", code=FieldErrorCode.PATTERN),
            FieldError(name="enrolmentId", code=FieldErrorCode.MIN_LENGTH),
        ),
    )

    block = form_error_block(errors, definition, load_clarification_text())

    assert block == FormErrorBlock(
        form_id="offer-1",
        context=_PMFBY_SUPPORT,
        submission_id="submission-1",
        fields=(
            FieldErrorEntry(
                name="applicantMobile",
                label="Applicant mobile",
                error="Applicant mobile is not in the right format.",
            ),
            FieldErrorEntry(
                name="enrolmentId",
                label="Enrolment id",
                error="Enrolment id must be at least 10 characters.",
            ),
        ),
    )


def test_a_field_off_the_form_has_no_label_and_is_named_in_the_message() -> None:
    definition = _forms().find(_PMFBY_SUPPORT)
    assert definition is not None
    errors = FormErrors(
        form_id="offer-1",
        context=_PMFBY_SUPPORT,
        submission_id="submission-1",
        definition=definition,
        field_errors=(FieldError(name="village", code=FieldErrorCode.UNKNOWN_FIELD),),
    )

    block = form_error_block(errors, definition, load_clarification_text())

    assert block.fields == (
        FieldErrorEntry(
            name="village", label="", error="village is not part of this form."
        ),
    )


def test_withholding_keeps_the_field_names_and_drops_every_value() -> None:
    submission = _submission({"applicantMobile": "9876543210", "season": "Kharif"})

    withheld = withhold_values(submission)

    assert withheld.values == (
        FieldValue(name="applicantMobile", value=WITHHELD),
        FieldValue(name="season", value=WITHHELD),
    )
    assert (withheld.form_id, withheld.context, withheld.submission_id) == (
        submission.form_id,
        submission.context,
        submission.submission_id,
    )
    assert "9876543210" not in withheld.model_dump_json()


def test_an_unknown_field_sent_twice_is_listed_once() -> None:
    submission = _submission(_PMFBY_VALUES)
    twice = submission.model_copy(
        update={
            "values": (
                *submission.values,
                FieldValue(name="village", value="Anand"),
                FieldValue(name="village", value="Nadiad"),
            )
        }
    )

    outcome = validate_submission(twice, _forms())

    assert isinstance(outcome, FormErrors)
    assert outcome.field_errors == (
        FieldError(name="village", code=FieldErrorCode.DUPLICATE),
    )


def test_a_pattern_python_cannot_compile_is_refused_at_parse() -> None:
    """JSON Schema patterns are ECMA 262; a pack using a construct Python's
    `re` lacks must fail at boot, not on the farmer's submission."""
    attributes = yaml.safe_load(_GRIEVANCE_YAML)
    leaf = attributes["components"]["schemas"]["PMFBYSupport"]
    leaf["allOf"][1]["properties"]["cropYear"]["pattern"] = r"^\p{N}{4}$"

    with pytest.raises(ValueError, match="cropYear"):
        parse_api_pack(_pack(yaml.safe_dump(attributes)))


def test_a_field_carries_the_pii_class_the_pack_marks_it_with() -> None:
    schema = _schema_for(_PMFBY_SUPPORT)

    assert _field(schema, "grievance.description").pii_class == PiiClass.FREETEXT
    assert _field(schema, "applicantMobile").pii_class == PiiClass.CONTACT
    assert _field(schema, "cropYear").pii_class is None


def test_the_free_text_of_a_submission_is_its_freetext_fields_only() -> None:
    valid = validate_submission(_submission(_PMFBY_VALUES), _forms())
    assert isinstance(valid, ValidSubmission)

    assert free_text_of(valid) == (_PMFBY_VALUES["grievance.description"],)


def test_only_the_desks_advertising_the_scheme_are_kept() -> None:
    pmfby = grievance_provider("pmfby-desk", "PMFBY")
    kisan = grievance_provider("kisan-desk", "PM-KISAN")
    unmarked = ProviderCapability(
        provider_id="plain",
        provider_name="PLAIN",
        capability="openagrinet:Grievance",
        resource_id="res:plain:grievance",
    )

    kept = candidates_for_scheme((kisan, pmfby, unmarked), "PMFBY")

    assert kept == (pmfby,)


_FILE_GRIEVANCE_EXAMPLE = json.loads(
    (
        Path(__file__).parents[3]
        / "fixtures"
        / "api_packs"
        / "pmfby_file_grievance_example.json"
    ).read_text(encoding="utf-8")
)


def test_the_body_holds_every_must_send_path_from_pack_provider_and_form() -> None:
    """Compared with the pack's published example, less the `maySend` fields
    this slice does not send."""

    schema = _schema_for(_PMFBY_SUPPORT)
    provider = ProviderCapability(
        provider_id="pmfby",
        provider_name="PMFBY Grievance Portal",
        capability="openagrinet:Grievance",
        resource_id="res:pmfby:grievance",
    )
    values = {
        "enrolmentId": "KA2026KH00123456",
        "applicantMobile": "9876543210",
        "cropYear": "2026",
        "season": "Kharif",
        "grievance.description": (
            "Cannot log in to the PMFBY portal to view my Kharif 2026 enrolment."
        ),
    }

    body = build_action_attributes(schema, provider, values)

    expected = {
        key: value
        for key, value in _FILE_GRIEVANCE_EXAMPLE.items()
        if key not in ("complaintDate", "receiptSourceId")
    }
    expected["grievance"] = {
        "description": _FILE_GRIEVANCE_EXAMPLE["grievance"]["description"]
    }
    expected["scheme"] = {"code": "PMFBY"}
    assert body == expected


def test_a_provider_path_nobody_can_fill_is_refused_at_parse() -> None:
    """A pack defect must stop the boot, not the farmer's filing."""

    attributes = yaml.safe_load(_GRIEVANCE_YAML)
    leaf = attributes["components"]["schemas"]["PMFBYSupport"]
    leaf["x-oan-fields"]["mustSend"].append("provider.descriptor.code")
    provider = leaf["allOf"][1]["properties"]["provider"]
    provider["allOf"] = [{"properties": {"descriptor": {"properties": {"code": {}}}}}]

    with pytest.raises(ValueError, match="provider.descriptor.code"):
        parse_api_pack(_pack(yaml.safe_dump(attributes)))


def test_a_pack_that_names_no_jsonld_type_is_refused() -> None:
    """Every call carries `@type`; a pack without it could not be filed."""

    attributes = yaml.safe_load(_GRIEVANCE_YAML)
    del attributes["components"]["schemas"]["Grievance"]["x-jsonld"]

    with pytest.raises(ValueError, match="x-jsonld"):
        parse_api_pack(_pack(yaml.safe_dump(attributes)))


def test_the_reply_keeps_what_the_provider_added_not_what_the_farmer_typed() -> None:
    schema = _schema_for(_PMFBY_SUPPORT)
    reply = {
        "@type": "openagrinet:Grievance",
        "scheme": {"code": "PMFBY"},
        "enrolmentId": "KA2026KH00123456",
        "applicantMobile": "9876543210",
        "grievance": {"description": "No payment yet.", "category": {"code": "3"}},
        "case": {"ticketNo": "100626000099001"},
    }

    kept = without_farmer_values(reply, schema)

    assert kept == {
        "@type": "openagrinet:Grievance",
        "scheme": {"code": "PMFBY"},
        "grievance": {"category": {"code": "3"}},
        "case": {"ticketNo": "100626000099001"},
    }
