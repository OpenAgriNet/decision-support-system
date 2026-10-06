"""The regex identifier, behind the ``PiiIdentifier`` port.

Every rule runs over each text. A pattern runs over both the original text and a
copy with number gaps joined: the copy catches ``98765 43210``; the original
catches a phone the copy has glued to the next number (``9876543210 2 acre`` →
``98765432102``). A match only the copy found is dropped when it swallows a
number written whole. Other overlaps are left for core's ``resolve`` to settle.

Pure ``re`` and well under a millisecond a text, so it runs on the event loop
rather than a worker thread.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence

from dss.adapters.pii_identifier.regex.models import (
    DeclaringPhraseRule,
    Normalisation,
    PatternRule,
    RegexSettings,
)
from dss.adapters.pii_identifier.regex.validators import is_valid
from dss.core.redaction.models import PiiSpan
from dss.core.redaction.normalise import Shadow, join_number_gaps

# One word after a declaring phrase: letters, with an inner ' . or - (O'Neil).
_NAME_TOKEN = re.compile(r"[ \t]+([^\W\d_]+(?:['.-][^\W\d_]+)*)")
# Digits, gaps and a leading + only: a number, not an email or a code.
_NUMBER = re.compile(r"\+?[\d \-]+")


class RegexIdentifier:
    name = "regex"

    def __init__(self, settings: RegexSettings) -> None:
        self._settings = settings
        # Compiled once here, at boot, not on every turn.
        self._compiled: list[re.Pattern[str]] = [
            _compile(rule) for rule in settings.rules
        ]

    async def identify(self, texts: Sequence[str]) -> list[list[PiiSpan]]:
        return [self._identify_one(text) for text in texts]

    def _identify_one(self, text: str) -> list[PiiSpan]:
        normalisation = self._settings.normalisation
        joined = _join_gaps(text, normalisation)
        # Each span with whether it was found only by joining gaps.
        found: list[tuple[PiiSpan, bool]] = []
        for rule, compiled in zip(self._settings.rules, self._compiled, strict=True):
            if isinstance(rule, PatternRule):
                found.extend(
                    _match_pattern(rule, compiled, text, joined, normalisation)
                )
            else:
                found.extend((s, False) for s in _match_phrase(rule, compiled, text))
        return _drop_glued(found)


def _compile(rule: PatternRule | DeclaringPhraseRule) -> re.Pattern[str]:
    if isinstance(rule, PatternRule):
        return re.compile(rule.pattern)
    phrases = sorted(rule.phrases, key=len, reverse=True)
    return re.compile(
        r"(?<!\w)(?:" + "|".join(re.escape(p) for p in phrases) + r")(?!\w)",
        re.IGNORECASE,
    )


def _match_pattern(
    rule: PatternRule,
    compiled: re.Pattern[str],
    text: str,
    joined: Shadow,
    normalisation: Normalisation,
) -> Iterator[tuple[PiiSpan, bool]]:
    seen: set[tuple[int, int]] = set()

    for match in compiled.finditer(text):
        span = match.span()
        if span[0] == span[1]:
            continue
        seen.add(span)
        # A number is kept with its gaps joined, the one form a provider is
        # sent. Anything else — an email — is kept exactly as written.
        canonical = match.group()
        if _NUMBER.fullmatch(canonical):
            canonical = _join_gaps(canonical, normalisation).text
        if is_valid(rule.validator, canonical):
            yield _to_span(rule, span, canonical), False

    if joined.text == text:
        return
    for match in compiled.finditer(joined.text):
        if match.start() == match.end():
            continue
        span = joined.map_to_original(match.start(), match.end())
        if span in seen:
            continue
        seen.add(span)
        if is_valid(rule.validator, match.group()):
            yield _to_span(rule, span, match.group()), True


def _drop_glued(found: list[tuple[PiiSpan, bool]]) -> list[PiiSpan]:
    """Drop a match found only by joining gaps when it swallows another kind of
    number the farmer wrote whole. "9876543210 102" joins into a run that can
    pass the card check; the phone was written apart from "102", so the phone
    stands. The same kind growing is fine: "0091 98765 43210" is one phone."""

    whole = [s for s, glued in found if not glued]
    return [
        span
        for span, glued in found
        if not glued
        or not any(
            other.entity != span.entity
            and span.start <= other.start
            and other.end <= span.end
            for other in whole
        )
    ]


def _match_phrase(
    rule: DeclaringPhraseRule, announce: re.Pattern[str], text: str
) -> Iterator[PiiSpan]:
    stop = {word.lower() for word in rule.stopwords}

    for match in announce.finditer(text):
        tokens: list[re.Match[str]] = []
        position = match.end()
        while len(tokens) < rule.max_tokens:
            token = _NAME_TOKEN.match(text, position)
            if token is None or token.group(1).lower() in stop:
                break
            tokens.append(token)
            position = token.end()
        if tokens:
            span = (tokens[0].start(1), tokens[-1].end(1))
            name = " ".join(t.group(1) for t in tokens)
            yield _to_span(rule, span, name)


def _join_gaps(text: str, normalisation: Normalisation) -> Shadow:
    return join_number_gaps(
        text,
        separators="".join(normalisation.join_separators),
        max_separators=normalisation.max_separators,
    )


def _to_span(
    rule: PatternRule | DeclaringPhraseRule, span: tuple[int, int], value: str
) -> PiiSpan:
    return PiiSpan(
        start=span[0],
        end=span[1],
        entity=rule.entity,
        score=1.0,
        source=RegexIdentifier.name,
        value=value,
    )
