"""Tier 1 — user-facing text rendered from a decision."""

from __future__ import annotations

from dss.core.moderation.messages import messages_for
from dss.core.moderation.models import (
    ModerationDecision,
    Outcome,
    ReasonCode,
)


def test_proceed_streams_the_warnings() -> None:
    decision = ModerationDecision(
        outcome=Outcome.PROCEED,
        sanitized_query="potato price",
        warnings=["Removed inappropriate language."],
    )
    assert messages_for(decision) == ["Removed inappropriate language."]


def test_clean_proceed_streams_nothing() -> None:
    assert messages_for(ModerationDecision(outcome=Outcome.PROCEED)) == []


def test_frustration_leads_with_an_empathetic_acknowledgement() -> None:
    decision = ModerationDecision(
        outcome=Outcome.PROCEED,
        sanitized_query="potato price",
        warnings=["Set aside the strong language."],
        frustration_detected=True,
    )
    messages = messages_for(decision)
    # empathy first, then the sanitization warning
    assert "frustrat" in messages[0].lower()
    assert messages[1] == "Set aside the strong language."


def test_delete_command_reject_says_it_is_malicious() -> None:
    decision = ModerationDecision(
        outcome=Outcome.REJECT,
        reason_code=ReasonCode.ROLE_OBFUSCATION,
        violated_policy_id="delete-command",
    )
    (message,) = messages_for(decision)
    assert "malicious" in message.lower()


def test_unavailable_reject_asks_to_retry() -> None:
    decision = ModerationDecision(
        outcome=Outcome.REJECT,
        reason_code=ReasonCode.MODERATION_UNAVAILABLE,
    )
    (message,) = messages_for(decision)
    assert "try again" in message.lower()
