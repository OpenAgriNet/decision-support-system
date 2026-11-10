from __future__ import annotations

from dss.core.forms.models import (
    FieldValue,
    FormContext,
    FormDefinition,
    FormEntry,
    FormStatus,
    FormRecords,
    FormSubmission,
)
from dss.core.forms.service import awaiting_init_for, clear_awaiting_init_record
from dss.core.provider_discovery.models import ProviderCapability
from tests.support.providers import grievance_provider

_PMKISAN_SUPPORT = FormContext(
    scheme="PM-KISAN", category="Grievance", action="support"
)
_PMKISAN_INIT = FormContext(scheme="PM-KISAN", category="Grievance", action="init")
_PMFBY_INIT = FormContext(scheme="PMFBY", category="Grievance", action="init")


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


def test_clearing_the_awaiting_init_record_keeps_done_ones() -> None:
    forms: FormRecords = {
        "open": _entry(FormStatus.AWAITING_INIT),
        "filed": _entry(FormStatus.DONE),
    }

    assert set(clear_awaiting_init_record(forms)) == {"filed"}


def test_awaiting_init_for_matches_scheme_and_category() -> None:
    forms: FormRecords = {
        "filed": _entry(FormStatus.DONE),
        "open": _entry(FormStatus.AWAITING_INIT),
    }

    assert awaiting_init_for(forms, _PMKISAN_INIT) == "open"


def test_awaiting_init_for_another_scheme_is_none() -> None:
    forms: FormRecords = {"open": _entry(FormStatus.AWAITING_INIT)}

    assert awaiting_init_for(forms, _PMFBY_INIT) is None


def test_awaiting_init_for_is_none_when_the_only_entry_is_done() -> None:
    forms: FormRecords = {"filed": _entry(FormStatus.DONE)}

    assert awaiting_init_for(forms, _PMKISAN_INIT) is None
