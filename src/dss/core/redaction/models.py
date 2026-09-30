"""Redaction rules (config) and the candidates a detector finds (runtime).

Everything a rule looks for is configuration: its pattern, which validator
confirms a match, its label, and whether the real value is kept for a provider
or destroyed. A bad rule fails when the config is built, which is at boot, and the
error names the rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dss.core.redaction.validators import VALIDATORS

# An entity name becomes part of a tag, «phone_1», so it must be tag-safe.
_ENTITY = r"^[a-z][a-z0-9_]*$"


class ValueHandling(StrEnum):
    KEEP = "keep"  # held for this turn so a provider can be sent it
    DESTROY = "destroy"  # replaced and never held — Aadhaar, card


class Normalise(BaseModel):
    """How the shadow copy joins numbers people write with gaps (``98765 43210``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    join_separators: list[Annotated[str, Field(min_length=1, max_length=1)]] = [
        " ",
        "-",
    ]
    # How many separators in a row may sit between two digits and still be joined.
    max_separators: int = Field(2, ge=0, le=3)


class _RuleBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entity: str = Field(pattern=_ENTITY)
    value: ValueHandling


class PatternRule(_RuleBase):
    """A regular expression, confirmed by a named validator."""

    kind: Literal["pattern"] = "pattern"
    pattern: str
    validator: str = "format"

    @model_validator(mode="after")
    def _check(self) -> PatternRule:
        try:
            re.compile(self.pattern)
        except re.error as exc:
            raise ValueError(
                f"rule '{self.entity}': pattern does not compile: {exc}"
            ) from exc
        if self.validator not in VALIDATORS:
            raise ValueError(
                f"rule '{self.entity}': unknown validator '{self.validator}' "
                f"(known: {', '.join(sorted(VALIDATORS))})"
            )
        return self


class DeclaringPhraseRule(_RuleBase):
    """A phrase that announces what follows — "my name is Ramesh Patel".

    Captures up to ``max_tokens`` words after the phrase, stopping early at a
    stopword or at anything that is not a word."""

    kind: Literal["declaring_phrase"] = "declaring_phrase"
    phrases: list[str] = Field(min_length=1)
    max_tokens: int = Field(3, ge=1, le=5)
    stopwords: list[str] = []


Rule = Annotated[PatternRule | DeclaringPhraseRule, Field(discriminator="kind")]


class RedactionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    normalise: Normalise = Normalise()
    rules: list[Rule] = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class Candidate:
    """A span a detector thinks should be replaced. Offsets are into the text as
    the farmer wrote it.

    ``value`` is the real value, written the one way it will be sent to a
    provider (``9876543210``, not ``98765 43210``). ``None`` means the value is
    destroyed: it gets a tag but is never held. ``score`` is 1.0 for a pattern;
    a model detector (#136) reports its own."""

    start: int
    end: int
    entity: str
    score: float
    source: str
    value: str | None = None
