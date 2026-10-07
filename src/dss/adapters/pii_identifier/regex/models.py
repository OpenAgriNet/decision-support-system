"""The regex identifier's settings — one ``type: regex`` entry in the rules file.

Each rule says what it looks for: a pattern confirmed by a named validator, or a
phrase that announces a name. Whether the value is kept or destroyed is not a
rule's business; the file's ``entities:`` policy decides that for every
identifier. A bad rule fails when these settings are built, at boot, and the
error names the rule.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dss.adapters.pii_identifier.regex.validators import VALIDATORS
from dss.core.redaction.models import EntityName
from dss.core.redaction.normalise import MAX_SEPARATORS, SEPARATORS


class Normalisation(BaseModel):
    """How number gaps are joined before patterns run (``98765 43210``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    join_separators: list[Annotated[str, Field(min_length=1, max_length=1)]] = list(
        SEPARATORS
    )
    # How many separators in a row may sit between two digits and still be joined.
    max_separators: int = Field(MAX_SEPARATORS, ge=0, le=3)


class PatternRule(BaseModel):
    """A regular expression, confirmed by a named validator.

    ``groupings`` lists the digit groups a number may be written in when it is
    found only by joining gaps — a card as 4 4 4 4, an Aadhaar as 4 4 4. Empty
    means any. A number written whole is never held to them."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entity: EntityName
    kind: Literal["pattern"] = "pattern"
    pattern: str
    validator: str = "format"
    groupings: list[list[int]] = []

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
        if any(not group or min(group) < 1 for group in self.groupings):
            raise ValueError(
                f"rule '{self.entity}': every grouping needs group sizes of 1 or more"
            )
        return self


class DeclaringPhraseRule(BaseModel):
    """A phrase that announces what follows — "my name is Ramesh Patel".

    Captures up to ``max_tokens`` words after the phrase, stopping early at a
    stopword or at anything that is not a word. A colon or dash after the
    phrase is skipped, and so is a title ("Dr.", "Shri"), which stays in the
    text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entity: EntityName
    kind: Literal["declaring_phrase"] = "declaring_phrase"
    phrases: list[str] = Field(min_length=1)
    max_tokens: int = Field(3, ge=1, le=5)
    stopwords: list[str] = []
    titles: list[str] = ["mr", "mrs", "ms", "dr", "shri", "sri", "smt", "kumari"]


Rule = Annotated[PatternRule | DeclaringPhraseRule, Field(discriminator="kind")]


class RegexSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["regex"] = "regex"
    normalisation: Normalisation = Normalisation()
    rules: list[Rule] = Field(min_length=1)
