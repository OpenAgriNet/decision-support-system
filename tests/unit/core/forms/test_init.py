from __future__ import annotations

import pytest

from dss.core.forms.models import FieldKind, FormContext, FormDefinition
from tests.support.grievance_pack import (
    PMFBY_STATUS,
    PMFBY_SUPPORT,
    PMKISAN_INIT,
    PMKISAN_STATUS,
    PMKISAN_SUPPORT,
    find_action_schema,
)


@pytest.mark.parametrize("context", [PMKISAN_SUPPORT, PMKISAN_STATUS])
def test_what_the_farmer_fills_under_challenge_waits_for_init(
    context: FormContext,
) -> None:
    schema = find_action_schema(context)

    assert schema.needs_init()
    assert [field.path for field in schema.init_fields()] == [
        "challenge.response.value"
    ]


@pytest.mark.parametrize("context", [PMKISAN_INIT, PMFBY_SUPPORT, PMFBY_STATUS])
def test_an_action_with_no_challenge_to_answer_needs_no_init(
    context: FormContext,
) -> None:
    assert not find_action_schema(context).needs_init()


def test_the_details_form_leaves_out_what_waits_for_init() -> None:
    schema = find_action_schema(PMKISAN_SUPPORT)

    form = FormDefinition.from_fields(schema.context, schema.request_fields())

    assert [field.name for field in form.fields] == [
        "enrolmentId",
        "grievance.category.code",
        "grievance.description",
    ]


def test_the_init_form_asks_only_for_the_response_and_hides_it() -> None:
    schema = find_action_schema(PMKISAN_SUPPORT)

    form = FormDefinition.from_fields(PMKISAN_INIT, schema.init_fields())

    [response] = form.fields
    assert response.name == "challenge.response.value"
    assert response.kind is FieldKind.SECRET
    assert response.rules.pattern == "^[0-9]{4}$"
