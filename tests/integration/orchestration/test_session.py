from __future__ import annotations

from dss.core.forms.models import (
    FormContext,
    FormDefinition,
    FormEntry,
    FormStatus,
    FormSubmission,
)
from dss.core.shared.session_state import SessionState
from dss.orchestration.session import Session

_GRIEVANCE = FormContext(scheme="PM-KISAN", category="Grievance", action="support")


# Stands in for a section another feature will add, like a farmer's location.
class _StateWithLocation(SessionState):
    farmer_location: str = ""


def _done_entry() -> FormEntry:
    return FormEntry(
        definition=FormDefinition(context=_GRIEVANCE, fields=()),
        submission=FormSubmission(
            form_id="f1", context=_GRIEVANCE, submission_id="s1", values=()
        ),
        status=FormStatus.DONE,
    )


def test_updating_forms_keeps_every_other_section_as_loaded() -> None:
    session = Session(_StateWithLocation(farmer_location="Nashik"), is_kept=True)

    session.update_forms({"f1": _done_entry()})

    assert session.state == _StateWithLocation(
        farmer_location="Nashik", forms={"f1": _done_entry()}
    )


def test_a_session_has_changed_only_once_its_forms_do() -> None:
    session = Session(SessionState(), is_kept=True)
    assert not session.changed

    session.update_forms({"f1": _done_entry()})

    assert session.changed
