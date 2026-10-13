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

# RFC 5646's main form: language, script, region, variants. Extlang,
# extensions and private use are left out — no channel sends them.
_TAG = re.compile(
    r"(?P<language>[A-Za-z]{2,3})"
    r"(?:-(?P<script>[A-Za-z]{4}))?"
    r"(?:-(?P<region>[A-Za-z]{2}|[0-9]{3}))?"
    r"(?P<variants>(?:-(?:[A-Za-z0-9]{5,8}|[0-9][A-Za-z0-9]{3}))*)"
)


class LanguageTag(RootModel[str]):
    """A BCP-47 language tag in standard casing: `gu`, `hi-IN`, `zh-Hant-TW`."""

    model_config = ConfigDict(frozen=True)

    @field_validator("root")
    @classmethod
    def _standard_form(cls, value: str) -> str:
        match = _TAG.fullmatch(value)
        if match is None:
            raise ValueError(
                f"language code {value!r} is not BCP-47 — use 'gu'/'hi'/'en', "
                "never a full name like 'gujarati'"
            )
        return _in_standard_casing(match)

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
        return tuple(
            LanguageTag("-".join(subtags[:length]))
            for length in range(len(subtags), 0, -1)
        )

    def is_within(self, broader: LanguageTag) -> bool:
        """Whether this tag is `broader` or a narrower form of it: `en-IN` is
        within `en`; `en-US` is not within `en-GB`."""
        return broader in self.lookup_order


def _in_standard_casing(match: re.Match[str]) -> str:
    subtags = [
        match["language"].lower(),
        (match["script"] or "").title(),
        (match["region"] or "").upper(),
        match["variants"].lower().lstrip("-"),
    ]
    return "-".join(subtag for subtag in subtags if subtag)


ENGLISH = LanguageTag("en")
