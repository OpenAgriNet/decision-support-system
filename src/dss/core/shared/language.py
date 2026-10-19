"""A language tag (BCP-47) as its own type.

A `RootModel` rather than an alias: a tag has a shape worth checking and
behaviour every user of it shares (its language, the order to fall back in),
yet on the wire and in `turns.jsonl` it stays a plain string.

Only what any tag means lives here. Which language the DSS treats as English,
or falls back to, is policy and stays with the code that applies it.
"""

from __future__ import annotations

import re

from pydantic import ConfigDict, RootModel, field_validator

# The shapes the DSS has always accepted: a 2-3 letter language, then any
# subtags of 1-8 letters or digits. Narrowing this to RFC 5646's main form
# would refuse extension tags (en-US-u-mu-celsius) that channels may send;
# that waits on the channels' answer (#174).
_TAG = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{1,8})*")


class LanguageTag(RootModel[str]):
    """A BCP-47 language tag in standard casing: `gu`, `hi-IN`, `zh-Hant-TW`."""

    model_config = ConfigDict(frozen=True)

    # A tag passed where a str is expected would fail the root's str check.
    @field_validator("root", mode="before")
    @classmethod
    def _unwrap_tag(cls, value: object) -> object:
        return value.root if isinstance(value, LanguageTag) else value

    @field_validator("root")
    @classmethod
    def _normalize_to_standard_form(cls, value: str) -> str:
        if _TAG.fullmatch(value) is None:
            raise ValueError(
                f"language tag {value!r} is not BCP-47 — use 'gu'/'hi'/'en', "
                "never a full name like 'gujarati'"
            )
        return _format_in_standard_casing(value)

    def __str__(self) -> str:
        return self.root

    # Pydantic adds the same hash to frozen models; declared so a type checker
    # sees a tag can be a dict key.
    def __hash__(self) -> int:
        return hash(self.root)

    @property
    def primary(self) -> str:
        """The language subtag (ISO 639): `gu` for `gu-Gujr-IN`."""
        return self.root.split("-", 1)[0]

    @property
    def lookup_order(self) -> tuple[LanguageTag, ...]:
        """This tag, then each shorter form (RFC 4647 §3.4 Lookup):
        `gu-Gujr-IN`, `gu-Gujr`, `gu`."""
        subtags = self.root.split("-")
        shorter_forms = (
            "-".join(subtags[:length])
            for length in range(len(subtags) - 1, 0, -1)
            # RFC 4647: a shorter form never ends on a singleton such as `-u`.
            if len(subtags[length - 1]) > 1
        )
        return (self, *(LanguageTag(form) for form in shorter_forms))

    def is_within(self, broader: LanguageTag) -> bool:
        """Whether this tag is `broader` or a narrower form of it: `en-IN` is
        within `en`; `en-US` is not within `en-GB`."""
        return broader in self.lookup_order


def _format_in_standard_casing(tag: str) -> str:
    """RFC 5646 §2.1.1 casing. Everything from the first singleton (an
    extension such as `-u-`, or private use `-x-`) on is lowercase."""
    subtags = tag.split("-")
    first_singleton = next(
        (index for index, subtag in enumerate(subtags) if index and len(subtag) == 1),
        len(subtags),
    )
    head, tail = subtags[:first_singleton], subtags[first_singleton:]
    cased = [
        head[0].lower(),
        *(_case_by_shape(subtag) for subtag in head[1:]),
        *(subtag.lower() for subtag in tail),
    ]
    return "-".join(cased)


def _case_by_shape(subtag: str) -> str:
    """A 4-letter subtag is a script (title case), a 2-letter one a region
    (upper case); the rest are lower case."""
    if subtag.isalpha() and len(subtag) == 4:
        return subtag.title()
    if subtag.isalpha() and len(subtag) == 2:
        return subtag.upper()
    return subtag.lower()


ENGLISH = LanguageTag("en")
