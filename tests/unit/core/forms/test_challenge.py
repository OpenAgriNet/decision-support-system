"""Tier 1 — reading the challenge a provider's init reply says it sent."""

from __future__ import annotations

import pytest

from dss.core.forms.challenge import (
    ChallengeMethod,
    IssuedChallenge,
    read_issued_challenge,
)


def test_an_otp_challenge_is_read_with_its_method() -> None:
    attributes = {"challenge": {"issued": {"method": "OTP", "channel": "SMS"}}}

    challenge = read_issued_challenge(attributes)

    assert challenge is not None
    assert challenge.method is ChallengeMethod.OTP
    assert challenge.unknown_method is None


@pytest.mark.parametrize(
    "issued", [{"method": "FACE"}, {}], ids=["unknown-method", "no-method"]
)
def test_a_challenge_with_no_known_method_is_still_issued(
    issued: dict,
) -> None:
    attributes = {"challenge": {"issued": issued}}

    assert read_issued_challenge(attributes).method is None


def test_a_method_we_do_not_know_is_kept_as_sent() -> None:
    attributes = {"challenge": {"issued": {"method": "FACE"}}}

    assert read_issued_challenge(attributes) == IssuedChallenge(sent_method="FACE")
    assert IssuedChallenge(sent_method="FACE").unknown_method == "FACE"


@pytest.mark.parametrize(
    "attributes",
    [{}, {"challenge": {}}, {"challenge": "OTP"}, {"challenge": {"issued": "OTP"}}],
)
def test_a_reply_without_an_issued_challenge_has_none(attributes: dict) -> None:
    assert read_issued_challenge(attributes) is None
