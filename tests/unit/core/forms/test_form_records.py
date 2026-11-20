from __future__ import annotations

from datetime import UTC, datetime, timedelta

from dss.core.forms.challenge import ChallengeMethod
from dss.core.forms.models import (
    FieldValue,
    FormAwaitingInit,
    FormContext,
    FormDone,
    FormRecords,
    FormSubmission,
)
from dss.core.forms.service import (
    WITHHELD,
    clear_awaiting_init_record,
    find_awaiting_init,
    is_done,
    with_form_done,
    with_init_awaited,
)
from tests.support.providers import grievance_provider

_PMKISAN_SUPPORT = FormContext(
    scheme="PM-KISAN", category="Grievance", action="support"
)
_PMKISAN_INIT = FormContext(scheme="PM-KISAN", category="Grievance", action="init")
_PMFBY_INIT = FormContext(scheme="PMFBY", category="Grievance", action="init")
_NOW = datetime(2026, 11, 10, 9, 30, tzinfo=UTC)
_PROVIDER = grievance_provider("pmkisan", "PM-KISAN")


def _submission(form_id: str = "f1") -> FormSubmission:
    return FormSubmission(
        form_id=form_id,
        context=_PMKISAN_SUPPORT,
        submission_id="s1",
        values=(FieldValue(name="grievance.description", value="no money"),),
    )


def _waiting(form_id: str = "f1") -> FormAwaitingInit:
    return FormAwaitingInit(
        submission=_submission(form_id),
        provider=_PROVIDER,
        challenge_method=ChallengeMethod.OTP,
        awaited_at=_NOW,
    )


def _done(form_id: str = "f1") -> FormDone:
    return FormDone(submission=_submission(form_id))


def test_clearing_the_awaiting_init_record_keeps_done_ones() -> None:
    forms: FormRecords = {"open": _waiting("open"), "filed": _done("filed")}

    assert set(clear_awaiting_init_record(forms)) == {"filed"}


def test_the_form_awaiting_init_is_found_by_scheme_and_category() -> None:
    waiting = _waiting("open")
    forms: FormRecords = {"filed": _done("filed"), "open": waiting}

    assert find_awaiting_init(forms, _PMKISAN_INIT) == waiting


def test_no_form_awaits_init_for_another_scheme() -> None:
    forms: FormRecords = {"open": _waiting("open")}

    assert find_awaiting_init(forms, _PMFBY_INIT) is None


def test_no_form_awaits_init_when_the_only_entry_is_done() -> None:
    forms: FormRecords = {"filed": _done("filed")}

    assert find_awaiting_init(forms, _PMKISAN_INIT) is None


def test_a_form_is_done_only_with_a_done_entry() -> None:
    forms: FormRecords = {"filed": _done("filed"), "open": _waiting("open")}

    assert is_done(forms, "filed")
    assert not is_done(forms, "open")
    assert not is_done(forms, "never-sent")


def test_a_done_form_is_kept_under_its_form_id_with_no_value() -> None:
    forms: FormRecords = {"f1": _waiting("f1"), "filed": _done("filed")}

    done = with_form_done(forms, _submission("f1"))

    assert set(done) == {"f1", "filed"}
    assert isinstance(done["f1"], FormDone)
    assert done["f1"].submission.context == _PMKISAN_SUPPORT
    assert [value.value for value in done["f1"].submission.values] == [WITHHELD]


def test_a_form_awaiting_init_keeps_its_submission_provider_and_method() -> None:
    waiting = with_init_awaited(
        {},
        _submission("f1"),
        provider=_PROVIDER,
        challenge_method=ChallengeMethod.OTP,
        now=_NOW,
    )

    assert waiting == {"f1": _waiting("f1")}


def test_an_entry_awaited_again_differs_from_the_one_before() -> None:
    first = with_init_awaited(
        {}, _submission(), provider=_PROVIDER, challenge_method=None, now=_NOW
    )

    again = with_init_awaited(
        first,
        _submission(),
        provider=_PROVIDER,
        challenge_method=None,
        now=_NOW + timedelta(seconds=1),
    )

    assert again != first
