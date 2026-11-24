"""Tier 1 — reading the challenge a provider's init reply says it sent."""

from __future__ import annotations

import pytest

from dss.core.forms.challenge import (
    ChallengeMethod,
    InitReplyWithoutChallenge,
    UnknownChallenge,
    read_issued_challenge,
)


def test_an_otp_challenge_is_read_with_its_method() -> None:
    attributes = {"challenge": {"issued": {"method": "OTP", "channel": "SMS"}}}

    assert read_issued_challenge(attributes) is ChallengeMethod.OTP


@pytest.mark.parametrize(
    ("issued", "sent_method"),
    [({"method": "FACE"}, "FACE"), ({}, None)],
    ids=["unknown-method", "no-method"],
)
def test_a_challenge_with_no_known_method_is_still_issued(
    issued: dict, sent_method: str | None
) -> None:
    attributes = {"challenge": {"issued": issued}}

    assert read_issued_challenge(attributes) == UnknownChallenge(sent_method)


@pytest.mark.parametrize(
    "attributes",
    [{}, {"challenge": {}}, {"challenge": "OTP"}, {"challenge": {"issued": "OTP"}}],
)
def test_a_reply_without_an_issued_challenge_says_so(attributes: dict) -> None:
    assert read_issued_challenge(attributes) == InitReplyWithoutChallenge()
