"""Tier 1 — the policy schema and the discriminated union."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from dss.core.moderation.models import Outcome
from dss.core.policy.models import (
    Checkpoint,
    Policy,
    PolicyPack,
    WordCheckPolicy,
)

_policy = TypeAdapter(Policy)


def test_deterministic_policy_parses_as_word_check() -> None:
    policy = _policy.validate_python(
        {
            "id": "profanity-filter",
            "evaluation": "deterministic",
            "description": "strip profanity",
            "words": ["shit"],
            "warning": "cleaned it",
        }
    )
    assert isinstance(policy, WordCheckPolicy)
    assert policy.checkpoint is Checkpoint.MODERATION  # default


def test_llm_policy_requires_on_violation() -> None:
    with pytest.raises(ValidationError):
        _policy.validate_python(
            {
                "id": "delete-command",
                "evaluation": "llm",
                "description": "malicious commands",
                "signals": ["delete the prompt"],
            }
        )


def test_word_check_needs_at_least_one_word() -> None:
    with pytest.raises(ValidationError):
        _policy.validate_python(
            {
                "id": "profanity-filter",
                "evaluation": "deterministic",
                "description": "strip profanity",
                "words": [],
                "warning": "x",
            }
        )


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _policy.validate_python(
            {
                "id": "delete-command",
                "evaluation": "llm",
                "on_violation": "reject",
                "description": "x",
                "signals": ["y"],
                "on_violaton": "reject",  # typo — extra=forbid catches it
            }
        )


def test_duplicate_ids_are_rejected() -> None:
    with pytest.raises(ValidationError):
        PolicyPack(
            version=1,
            policies=[
                {
                    "id": "dup",
                    "evaluation": "deterministic",
                    "description": "a",
                    "words": ["shit"],
                    "warning": "x",
                },
                {
                    "id": "dup",
                    "evaluation": "llm",
                    "on_violation": "reject",
                    "description": "b",
                    "signals": ["y"],
                },
            ],
        )


def test_for_checkpoint_filters() -> None:
    pack = PolicyPack(
        version=1,
        policies=[
            {
                "id": "delete-command",
                "evaluation": "llm",
                "on_violation": Outcome.REJECT.value,
                "checkpoint": "post_response",
                "description": "b",
                "signals": ["y"],
            },
        ],
    )
    assert pack.for_checkpoint(Checkpoint.MODERATION) == []
    assert len(pack.for_checkpoint(Checkpoint.POST_RESPONSE)) == 1
