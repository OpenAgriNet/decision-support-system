"""Tier 1 — the ModerationDecision contract."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dss.core.moderation.models import (
    ModerationDecision,
    Outcome,
    ReasonCode,
)


def test_non_proceed_requires_a_reason_code() -> None:
    with pytest.raises(ValidationError):
        ModerationDecision(outcome=Outcome.REJECT)


def test_reject_with_reason_is_valid() -> None:
    decision = ModerationDecision(
        outcome=Outcome.REJECT,
        reason_code=ReasonCode.ROLE_OBFUSCATION,
        violated_policy_id="delete-command",
    )
    assert decision.outcome is Outcome.REJECT


def test_sanitized_query_on_a_reject_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ModerationDecision(
            outcome=Outcome.REJECT,
            reason_code=ReasonCode.ROLE_OBFUSCATION,
            sanitized_query="cleaned",
        )


def test_warnings_on_a_reject_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ModerationDecision(
            outcome=Outcome.REJECT,
            reason_code=ReasonCode.ROLE_OBFUSCATION,
            warnings=["something"],
        )


def test_proceed_may_carry_sanitization() -> None:
    decision = ModerationDecision(
        outcome=Outcome.PROCEED,
        sanitized_query="I want to know potato price",
        warnings=["Removed inappropriate language."],
    )
    assert decision.reason_code is None
    assert decision.sanitized_query == "I want to know potato price"


def test_plain_proceed_is_valid() -> None:
    decision = ModerationDecision(outcome=Outcome.PROCEED)
    assert decision.warnings == []
    assert decision.sanitized_query is None
