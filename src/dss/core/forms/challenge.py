"""What a provider's init reply says it sent the farmer to answer."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

# The pack does not say what init returns yet; v2 packs will. Until then:
# what the init form answers is issued under `challenge.issued`.
_ISSUED_PATH = ("challenge", "issued")


class ChallengeMethod(StrEnum):
    OTP = "OTP"


@dataclass(frozen=True)
class IssuedChallenge:
    # None: a method this DSS does not know.
    method: ChallengeMethod | None


def read_issued_challenge(attributes: Mapping[str, object]) -> IssuedChallenge | None:
    issued = _issued_in(attributes)
    if issued is None:
        return None
    return IssuedChallenge(method=_method_of(issued.get("method")))


def _issued_in(attributes: Mapping[str, object]) -> Mapping[str, object] | None:
    node: object = attributes
    for segment in _ISSUED_PATH:
        if not isinstance(node, Mapping):
            return None
        node = node.get(segment)
    return node if isinstance(node, Mapping) else None


def _method_of(value: object) -> ChallengeMethod | None:
    return ChallengeMethod(value) if value in ChallengeMethod else None
