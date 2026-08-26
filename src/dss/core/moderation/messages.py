"""User-facing text for a moderation decision.

Spec 0004 leaves the rejection message to the channel function; this is the
focused-build stand-in so the two policies produce something a user can read.
Text is rendered from the closed ``reason_code`` vocabulary (localizable later),
while ``warnings`` are already user-authored strings carried on the decision.

English only for this slice (spec 0004, "Language").
"""

from __future__ import annotations

from dss.core.moderation.models import ModerationDecision, Outcome, ReasonCode

_REJECT_MESSAGES: dict[ReasonCode, str] = {
    ReasonCode.ROLE_OBFUSCATION: (
        "This looks like a malicious command aimed at the assistant rather than a "
        "genuine question, so I can't act on it."
    ),
    ReasonCode.MODERATION_UNAVAILABLE: (
        "Something went wrong while checking your message. Please try again."
    ),
}

_GENERIC_REJECT = "I can't help with that request."


def messages_for(decision: ModerationDecision) -> list[str]:
    """Everything to stream to the user for this decision, in order.

    - PROCEED: any sanitization warnings (may be empty).
    - REJECT/CLARIFY/NO_MATCH: the rendered reason message.
    """

    if decision.outcome is Outcome.PROCEED:
        return list(decision.warnings)

    assert decision.reason_code is not None  # guaranteed by the decision validator
    return [_REJECT_MESSAGES.get(decision.reason_code, _GENERIC_REJECT)]
