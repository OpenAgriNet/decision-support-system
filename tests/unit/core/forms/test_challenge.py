"""Tier 1 — reading the challenge a provider's init reply says it sent."""

from __future__ import annotations

import pytest

from dss.core.forms.challenge import (
    InitReplyWithoutChallenge,
    UnknownChallenge,
    read_issued_challenge,
)
from dss.core.forms.models import ChallengeMethod


def test_an_otp_challenge_is_read_with_its_method() -> None:
    attributes = {"challenge": {"issued": {"method": "OTP", "channel": "SMS"}}}

    assert read_issued_challenge(attributes) is ChallengeMethod.OTP


def test_a_challenge_with_a_method_we_do_not_know_is_still_issued() -> None:
    attributes = {"challenge": {"issued": {"method": "FACE"}}}

    assert read_issued_challenge(attributes) == UnknownChallenge(sent_method="FACE")


@pytest.mark.parametrize(
    "attributes",
    [
        {},
        {"challenge": {}},
        {"challenge": "OTP"},
        {"challenge": {"issued": "OTP"}},
        {"challenge": {"issued": {}}},
        {"challenge": {"issued": {"method": 1}}},
    ],
    ids=["none", "empty", "text", "issued-text", "no-method", "method-not-text"],
)
def test_a_reply_without_an_issued_challenge_says_so(attributes: dict) -> None:
    assert read_issued_challenge(attributes) == InitReplyWithoutChallenge()
