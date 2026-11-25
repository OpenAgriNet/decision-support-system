"""What a provider's init reply says it sent the farmer to answer."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from dss.core.forms.models import ChallengeMethod, FormDefinition


@dataclass(frozen=True)
class UnknownChallenge:
    """Issued, but with a method this DSS does not know."""

    sent_method: str


@dataclass(frozen=True)
class InitReplyWithoutChallenge:
    pass


type IssuedChallenge = ChallengeMethod | UnknownChallenge


# The pack does not say what init returns yet; v2 packs will. Until then:
# what the init form answers is issued under `challenge.issued`.
class _Issued(BaseModel):
    method: str


class _Challenge(BaseModel):
    issued: _Issued


class _InitReplyAttributes(BaseModel):
    challenge: _Challenge


def read_issued_challenge(
    attributes: Mapping[str, object],
) -> IssuedChallenge | InitReplyWithoutChallenge:
    try:
        issued = _InitReplyAttributes.model_validate(attributes).challenge.issued
    except ValidationError:
        return InitReplyWithoutChallenge()
    if issued.method in ChallengeMethod:
        return ChallengeMethod(issued.method)
    return UnknownChallenge(sent_method=issued.method)


def known_method_of(challenge: IssuedChallenge) -> ChallengeMethod | None:
    return challenge if isinstance(challenge, ChallengeMethod) else None


# The pack has no titles yet, and its path in words reads badly to a farmer.
_OTP_FIELD = "challenge.response.value"
_OTP_LABEL = "Enter OTP"


def labelled_for(
    init_form: FormDefinition, method: ChallengeMethod | None
) -> FormDefinition:
    if method is ChallengeMethod.OTP:
        return init_form.with_label(_OTP_FIELD, _OTP_LABEL)
    return init_form
