"""The moderation decision type and its context (spec 0004).

Enforcement is structural, not prose: the service returns a ``ModerationDecision``
and the caller branches on ``outcome``. Nothing here decides what the farmer
reads — user-facing text is rendered from ``reason_code``/``warnings`` by the
channel function (see :mod:`dss.core.moderation.messages`).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dss.core.intent.models import Intent
from dss.core.shared.models import UserTurn


class Outcome(StrEnum):
    """Four outcomes. The fourth (``NO_MATCH``) is the one that gets lost when
    "we won't", "we didn't understand", and "we can't yet" are collapsed."""

    PROCEED = "proceed"
    REJECT = "reject"  # we will not answer this
    CLARIFY = "clarify"  # we did not understand — ask
    NO_MATCH = "no_match"  # understood; nothing here serves it


class ReasonCode(StrEnum):
    """A closed DSS vocabulary so metrics compare across deployments. Adopter
    policies cannot mint members — they report ``ADOPTER_POLICY`` plus their
    namespaced id in ``violated_policy_id``."""

    # harm — a policy fired
    UNSAFE_ILLEGAL = "unsafe_illegal"
    ROLE_OBFUSCATION = "role_obfuscation"
    POLITICAL_CONTROVERSIAL = "political_controversial"
    EXTERNAL_REFERENCE = "external_reference"
    # scope and comprehension — not harm
    DOMAIN_UNMAPPED = "domain_unmapped"
    INTENT_LOW_CONFIDENCE = "intent_low_confidence"
    UNSUPPORTED_ACTION_TYPE = "unsupported_action_type"
    # infrastructure — not the farmer's fault
    MODERATION_UNAVAILABLE = "moderation_unavailable"
    # any adopter-defined policy; violated_policy_id says which
    ADOPTER_POLICY = "adopter_policy"


class ModerationContext(BaseModel):
    """The roots a moderation policy may address: ``turn.*`` and ``intent.*``
    (spec 0003). A pure input bundle — nothing that a policy field-path cannot
    reach belongs here."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    turn: UserTurn
    intent: Intent = Field(default_factory=lambda: Intent(confidence=0.0))


class ModerationDecision(BaseModel):
    """The verdict. ``reason_code`` and ``violated_policy_id`` are separate fields
    so a timeout (``MODERATION_UNAVAILABLE``, no policy to attribute) reads
    differently from a policy rejection.

    ``sanitized_query`` and ``warnings`` extend spec 0004 for the redact-and-warn
    behaviour of the profanity filter (see ADR-0002): a turn can be cleaned and
    still proceed. They are only meaningful on ``PROCEED`` — harm never partitions,
    so a rejected turn carries no sanitized query.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: Outcome
    reason_code: ReasonCode | None = None
    violated_policy_id: str | None = None
    sanitized_query: str | None = None
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _reason_required_unless_proceed(self) -> ModerationDecision:
        if self.outcome is not Outcome.PROCEED and self.reason_code is None:
            raise ValueError(f"outcome {self.outcome!r} must carry a reason_code")
        return self

    @model_validator(mode="after")
    def _sanitize_only_when_proceeding(self) -> ModerationDecision:
        if self.outcome is not Outcome.PROCEED and (
            self.sanitized_query is not None or self.warnings
        ):
            raise ValueError(
                "sanitized_query/warnings are only valid on a PROCEED outcome — "
                "harm never partitions"
            )
        return self


class LlmModerationVerdict(BaseModel):
    """What the single batched LLM call returns: the id of the one violated LLM
    policy, or ``None`` when nothing fired. Kept minimal so the model has one job.
    """

    model_config = ConfigDict(extra="forbid")

    violated_policy_id: str | None = None
