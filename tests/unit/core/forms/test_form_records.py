from __future__ import annotations

from datetime import UTC, datetime, timedelta

from dss.core.forms.models import (
    FieldValue,
    FormContext,
    FormDefinition,
    FormEntry,
    FormRecords,
    FormStatus,
    FormSubmission,
    ValidSubmission,
)
from dss.core.forms.service import (
    WITHHELD,
    awaiting_init_for,
    clear_awaiting_init_record,
    is_done,
    with_call_filed,
    with_form_done,
    with_init_awaited,
)
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


_NOW = datetime(2026, 11, 10, 9, 30, tzinfo=UTC)


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


def test_a_form_is_done_only_with_a_done_entry() -> None:
    forms: FormRecords = {
        "filed": _entry(FormStatus.DONE),
        "open": _entry(FormStatus.AWAITING_INIT),
    }

    assert is_done(forms, "filed")
    assert not is_done(forms, "open")
    assert not is_done(forms, "never-sent")


def test_a_done_form_keeps_its_definition_and_no_value() -> None:
    entry = _entry(
        FormStatus.AWAITING_INIT, provider=grievance_provider("pmkisan", "PM-KISAN")
    )
    forms: FormRecords = {"open": entry, "filed": _entry(FormStatus.DONE)}

    done = with_form_done(forms, "open", entry.definition, entry.submission)

    assert set(done) == {"open", "filed"}
    assert done["open"].status is FormStatus.DONE
    assert done["open"].definition == entry.definition
    assert done["open"].provider is None
    assert done["open"].awaited_at is None
    assert [value.value for value in done["open"].submission.values] == [WITHHELD]


def test_a_filed_call_leaves_its_form_done_under_its_form_id() -> None:
    entry = _entry(FormStatus.AWAITING_INIT)
    details = ValidSubmission(
        definition=entry.definition,
        submission_id="s1",
        values={"grievance.description": "no money"},
    )

    filed = with_call_filed({}, details, entry.submission)

    assert filed == with_form_done({}, "f1", entry.definition, entry.submission)


def test_a_form_awaiting_init_keeps_its_submission_and_provider() -> None:
    provider = grievance_provider("pmkisan", "PM-KISAN")
    entry = _entry(FormStatus.DONE, provider=provider)

    waiting = with_init_awaited(
        {},
        "f1",
        entry.definition,
        entry.submission,
        provider=provider,
        now=_NOW,
    )

    assert waiting["f1"] == entry.model_copy(
        update={"status": FormStatus.AWAITING_INIT, "awaited_at": _NOW}
    )


def test_an_entry_awaited_again_differs_from_the_one_before() -> None:
    entry = _entry(FormStatus.AWAITING_INIT)
    provider = grievance_provider("pmkisan", "PM-KISAN")
    first = with_init_awaited(
        {},
        "f1",
        entry.definition,
        entry.submission,
        provider=provider,
        now=_NOW,
    )

    again = with_init_awaited(
        first,
        "f1",
        entry.definition,
        entry.submission,
        provider=provider,
        now=_NOW + timedelta(seconds=1),
    )

    assert again != first
