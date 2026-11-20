from __future__ import annotations

from datetime import UTC, datetime

from dss.core.forms.models import (
    FieldValue,
    FormAwaitingInit,
    FormContext,
    FormDone,
    FormSubmission,
)
from dss.core.shared.session_state import SessionState
from tests.support.providers import grievance_provider

_PMKISAN_SUPPORT = FormContext(
    scheme="PM-KISAN", category="Grievance", action="support"
)
_SUBMISSION = FormSubmission(
    form_id="f1",
    context=_PMKISAN_SUPPORT,
    submission_id="s1",
    values=(FieldValue(name="grievance.description", value="no money"),),
)
_NOW = datetime(2026, 11, 10, 9, 30, tzinfo=UTC)


def test_session_state_round_trips_through_json() -> None:
    state = SessionState(
        forms={
            "f1": FormAwaitingInit(
                submission=_SUBMISSION,
                provider=grievance_provider("pmkisan", "PM-KISAN"),
                awaited_at=_NOW,
            ),
            "f2": FormDone(submission=_SUBMISSION),
        }
    )

    restored = SessionState.model_validate_json(state.model_dump_json())

    assert restored == state


def test_an_entry_saved_before_the_challenge_method_still_reads() -> None:
    state = SessionState(
        forms={
            "f1": FormAwaitingInit(
                submission=_SUBMISSION,
                provider=grievance_provider("pmkisan", "PM-KISAN"),
                awaited_at=_NOW,
            )
        }
    )
    saved = state.model_dump(mode="json")
    del saved["forms"]["f1"]["challenge_method"]

    restored = SessionState.model_validate(saved)

    assert restored == state
