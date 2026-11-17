from __future__ import annotations

from dss.core.forms.models import (
    FieldValue,
    FormContext,
    FormDefinition,
    FormEntry,
    FormStatus,
    FormSubmission,
)
from dss.core.provider_discovery.models import ProviderCapability
from dss.core.shared.session_state import SessionState
from tests.support.providers import grievance_provider

_PMKISAN_SUPPORT = FormContext(
    scheme="PM-KISAN", category="Grievance", action="support"
)


def _entry(
    status: FormStatus, *, provider: ProviderCapability | None = None
) -> FormEntry:
    return FormEntry(
        definition=FormDefinition(context=_PMKISAN_SUPPORT, fields=()),
        submission=FormSubmission(
            form_id="f1",
            context=_PMKISAN_SUPPORT,
            submission_id="s1",
            values=(FieldValue(name="grievance.description", value="no money"),),
        ),
        status=status,
        provider=provider,
    )


def test_session_state_round_trips_through_json() -> None:
    state = SessionState(
        forms={
            "f1": _entry(
                FormStatus.AWAITING_INIT,
                provider=grievance_provider("pmkisan", "PM-KISAN"),
            ),
            "f2": _entry(FormStatus.DONE),
        }
    )

    restored = SessionState.model_validate_json(state.model_dump_json())

    assert restored == state
