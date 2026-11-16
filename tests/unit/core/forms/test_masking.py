from __future__ import annotations

from collections.abc import Mapping

import pytest

from dss.core.forms.models import (
    FieldDefinition,
    FieldKind,
    FieldValue,
    FormContext,
    FormDefinition,
    FormSubmission,
    PiiClass,
    ValidSubmission,
)
from dss.core.forms.service import mask_pii_fields, validate_submission
from tests.support.grievance_pack import (
    PMFBY_STATUS,
    PMFBY_SUPPORT,
    PMKISAN_INIT,
    PMKISAN_STATUS,
    PMKISAN_SUPPORT,
    build_grievance_forms,
)

_KISAN_REST = {
    "grievance.category.code": "G001",
    "grievance.description": "My last installment has not come.",
}
_PMFBY_SUPPORT_VALUES = {
    "enrolmentId": "PMFBY-2024-000123",
    "applicantMobile": "9876543210",
    "cropYear": "2024",
    "season": "Kharif",
    "grievance.description": "Claim for the Kharif crop loss has not been paid.",
}


def _valid(context: FormContext, values: Mapping[str, str]) -> ValidSubmission:
    submission = FormSubmission(
        form_id="offer-1",
        context=context,
        submission_id="submission-1",
        values=tuple(
            FieldValue(name=name, value=value) for name, value in values.items()
        ),
    )
    valid = validate_submission(submission, build_grievance_forms())
    assert isinstance(valid, ValidSubmission)
    return valid


def test_every_marked_field_is_masked_whole_and_the_rest_kept() -> None:
    masked = mask_pii_fields(_valid(PMFBY_SUPPORT, _PMFBY_SUPPORT_VALUES))

    assert masked.submission.values == {
        "enrolmentId": "«form_support_enrolment_id_1»",
        "applicantMobile": "«form_support_applicant_mobile_2»",
        "cropYear": "2024",
        "season": "Kharif",
        "grievance.description": "«form_support_grievance_description_5»",
    }


def test_each_tag_holds_the_value_it_replaced() -> None:
    masked = mask_pii_fields(_valid(PMFBY_SUPPORT, _PMFBY_SUPPORT_VALUES))

    assert masked.visibility.values == {
        "«form_support_enrolment_id_1»": "PMFBY-2024-000123",
        "«form_support_applicant_mobile_2»": "9876543210",
        "«form_support_grievance_description_5»": (
            "Claim for the Kharif crop loss has not been paid."
        ),
    }


def test_the_init_response_is_masked() -> None:
    masked = mask_pii_fields(_valid(PMKISAN_INIT, {"challenge.response.value": "1234"}))

    assert masked.submission.values == {
        "challenge.response.value": "«form_init_challenge_response_value_1»"
    }
    assert masked.visibility.reveal(masked.submission.values) == {
        "challenge.response.value": "1234"
    }


def test_the_init_response_is_revealed_but_never_searched_for_in_a_reply() -> None:
    masked = mask_pii_fields(_valid(PMKISAN_INIT, {"challenge.response.value": "2026"}))

    reply = {"case": {"filedOn": "2026-11-02"}, "year": 2026, "note": "2026"}

    assert masked.visibility.conceal_body(reply) == reply
    revealed = masked.visibility.reveal(masked.submission.values)
    assert revealed == {"challenge.response.value": "2026"}


def test_two_fields_whose_names_read_alike_get_different_tags() -> None:
    fields = tuple(
        FieldDefinition(
            name=name, label=name, kind=FieldKind.TEXT, pii_class=PiiClass.CONTACT
        )
        for name in ("farmer.mobile", "farmerMobile")
    )
    valid = ValidSubmission(
        definition=FormDefinition(context=PMFBY_SUPPORT, fields=fields),
        submission_id="submission-1",
        values={"farmer.mobile": "9876543210", "farmerMobile": "9123456780"},
    )

    masked = mask_pii_fields(valid)

    tags = masked.submission.values
    assert tags["farmer.mobile"] != tags["farmerMobile"]
    assert masked.visibility.reveal(tags) == valid.values


def test_a_details_form_and_its_init_form_never_share_a_tag() -> None:
    init_field = FieldDefinition(
        name="enrolmentId", label="id", kind=FieldKind.TEXT, pii_class="identifier"
    )
    init = ValidSubmission(
        definition=FormDefinition(context=PMKISAN_INIT, fields=(init_field,)),
        submission_id="submission-2",
        values={"enrolmentId": "PMK1234567"},
    )
    details = _valid(PMKISAN_SUPPORT, {"enrolmentId": "PMK1234567"} | _KISAN_REST)

    init_tags = mask_pii_fields(init).visibility.values.keys()
    details_tags = mask_pii_fields(details).visibility.values.keys()

    assert not init_tags & details_tags


# The fields each form's pack marks with `x-oan-pii`: a mark that stops being
# read would let a farmer's value into the logs.
@pytest.mark.parametrize(
    ("context", "marked"),
    [
        (
            PMFBY_SUPPORT,
            {"enrolmentId", "applicantMobile", "grievance.description"},
        ),
        (PMFBY_STATUS, {"applicantMobile"}),
        (PMKISAN_SUPPORT, {"enrolmentId", "grievance.description"}),
        (PMKISAN_STATUS, {"enrolmentId"}),
        (PMKISAN_INIT, {"challenge.response.value"}),
    ],
    ids=["pmfby-support", "pmfby-status", "pmkisan-support", "pmkisan-status", "init"],
)
def test_the_grievance_pack_marks_these_fields(
    context: FormContext, marked: set[str]
) -> None:
    form = build_grievance_forms().find(context)
    assert form is not None

    assert {
        field.name for field in form.fields if field.pii_class is not None
    } == marked
