from __future__ import annotations

from dss.core.forms.models import FormContext, FormDefinition
from dss.core.forms.service import offer_form

_GRIEVANCE_FORM = FormDefinition(
    context=FormContext(scheme="PMFBY", category="Grievance", action="support"),
    fields=(),
)


def test_each_offer_gets_its_own_id() -> None:
    first, second = offer_form(_GRIEVANCE_FORM), offer_form(_GRIEVANCE_FORM)

    assert first.form == second.form == _GRIEVANCE_FORM
    assert first.form_id and first.form_id != second.form_id
