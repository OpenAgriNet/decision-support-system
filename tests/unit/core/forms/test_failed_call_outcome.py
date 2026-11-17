from __future__ import annotations

import pytest

from dss.core.forms.models import FailedCallOutcome
from dss.core.forms.service import outcome_of_failed_call
from dss.core.planner.models import NO_READABLE_REPLY, Failure


def _failure(status_code: int | None, *, retryable: bool = False) -> Failure:
    return Failure(
        ask_index=0,
        capability="openagrinet:Grievance",
        reason="support failed",
        retryable=retryable,
        status_code=status_code,
    )


def test_no_failure_means_the_provider_answered() -> None:
    assert outcome_of_failed_call(None) is FailedCallOutcome.ANSWERED


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (400, FailedCallOutcome.REFUSED),
        (422, FailedCallOutcome.REFUSED),
        (408, FailedCallOutcome.NOT_SENT),
        (429, FailedCallOutcome.NOT_SENT),
        (None, FailedCallOutcome.NOT_SENT),
        (500, FailedCallOutcome.OUTCOME_UNKNOWN),
        (503, FailedCallOutcome.OUTCOME_UNKNOWN),
        (NO_READABLE_REPLY, FailedCallOutcome.OUTCOME_UNKNOWN),
    ],
)
def test_a_failure_is_read_from_its_status_code(
    status_code: int | None, expected: FailedCallOutcome
) -> None:
    assert outcome_of_failed_call(_failure(status_code)) is expected


def test_a_retryable_4xx_is_still_refused() -> None:
    assert (
        outcome_of_failed_call(_failure(404, retryable=True))
        is FailedCallOutcome.REFUSED
    )
