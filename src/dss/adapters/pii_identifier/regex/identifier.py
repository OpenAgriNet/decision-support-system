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
from collections.abc import Iterator

from dss.adapters.pii_identifier.regex.models import (
    DeclaringPhraseRule,
    PatternRule,
    RegexSettings,
)
from dss.adapters.pii_identifier.regex.validators import is_valid
from dss.core.redaction.models import PiiSpan
from dss.core.redaction.normalise import Shadow, gap_pattern, join_number_gaps
from dss.ports.pii_identifier import IdentifierUnavailable

# One word after a declaring phrase: letters, with an inner ' . or - (O'Neil).
_NAME_TOKEN = re.compile(r"[ \t]+([^\W\d_]+(?:['.-][^\W\d_]+)*)")
# A colon or dash between the phrase and the name, with no space before it.
_LEAD = re.compile(r"[ \t]*[:\-–]")
# Digits, gaps and a leading + only: a number, not an email or a code.
_NUMBER = re.compile(r"\+?[\d \-]+")
_DIGITS = re.compile(r"\d+")


class RegexIdentifier:
    name = "regex"

    def __init__(self, settings: RegexSettings) -> None:
        """Compiles every pattern once, here, at boot. One that does not
        compile raises ``IdentifierUnavailable`` naming its rule, which stops
        the boot."""

        self._settings = settings
        self._compiled: list[re.Pattern[str]] = [
            _compile(rule) for rule in settings.rules
        ]
        normalisation = settings.normalisation
        separators = "".join(normalisation.join_separators)
        self._gap = gap_pattern(separators, normalisation.max_separators)
        # Digit groups with joinable gaps between them: "98765 43210 102".
        self._run = (
            re.compile(
                rf"\d+(?:[{re.escape(separators)}]{{1,{normalisation.max_separators}}}\d+)+"
            )
            if self._gap is not None
            else None
        )

    async def identify(self, text: str) -> list[PiiSpan]:
        return self._identify_one(text)

    def _identify_one(self, text: str) -> list[PiiSpan]:
        joined = join_number_gaps(text, gap=self._gap)
        # Each span with whether it was found only by joining gaps.
        found: list[tuple[PiiSpan, bool]] = []
        for rule, compiled in zip(self._settings.rules, self._compiled, strict=True):
            if isinstance(rule, PatternRule):
                found.extend(
                    _match_pattern(rule, compiled, text, joined, self._gap, self._run)
                )
            else:
                found.extend((s, False) for s in _match_phrase(rule, compiled, text))
        return _drop_glued(found)


def _compile(rule: PatternRule | DeclaringPhraseRule) -> re.Pattern[str]:
    try:
        if isinstance(rule, PatternRule):
            return re.compile(rule.pattern)
        phrases = sorted(rule.phrases, key=len, reverse=True)
        return re.compile(
            r"(?<!\w)(?:" + "|".join(re.escape(p) for p in phrases) + r")(?!\w)",
            re.IGNORECASE,
        )
    except re.error as exc:
        raise IdentifierUnavailable(
            f"rule '{rule.entity}': pattern does not compile: {exc}"
        ) from exc


def _match_pattern(
    rule: PatternRule,
    compiled: re.Pattern[str],
    text: str,
    joined: Shadow,
    gap: re.Pattern[str] | None,
    run: re.Pattern[str] | None,
) -> Iterator[tuple[PiiSpan, bool]]:
    seen: set[tuple[int, int]] = set()
    kept: list[tuple[int, int]] = []

    for match in compiled.finditer(text):
        span = match.span()
        if span[0] == span[1]:
            continue
        seen.add(span)
        # A number is kept with its gaps joined, the one form a provider is
        # sent. Anything else — an email — is kept exactly as written.
        canonical = match.group()
        if _NUMBER.fullmatch(canonical):
            canonical = join_number_gaps(canonical, gap=gap).text
        if is_valid(rule.validator, canonical):
            kept.append(span)
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
        if rule.groupings and not _in_groups(text[span[0] : span[1]], rule):
            continue
        if is_valid(rule.validator, match.group()):
            kept.append(span)
            yield _to_span(rule, span, match.group()), True

    yield from _match_sub_runs(rule, compiled, text, run, seen, kept)


def _match_sub_runs(
    rule: PatternRule,
    compiled: re.Pattern[str],
    text: str,
    run: re.Pattern[str] | None,
    seen: set[tuple[int, int]],
    kept: list[tuple[int, int]],
) -> Iterator[tuple[PiiSpan, bool]]:
    """Numbers made of only some of a run's digit groups. Joining
    "98765 43210 102" whole gives 13 digits, inside which no phone can match;
    its first two groups are the phone."""

    if run is None:
        return
    for found_run in run.finditer(text):
        groups = list(_DIGITS.finditer(text, found_run.start(), found_run.end()))
        for first in range(len(groups)):
            for last in range(first + 1, len(groups)):
                if first == 0 and last == len(groups) - 1:
                    continue  # the whole run: the joined pass already tried it
                span = (groups[first].start(), groups[last].end())
                digits = "".join(g.group() for g in groups[first : last + 1])
                inside = any(a <= span[0] and span[1] <= b for a, b in kept)
                if span in seen or inside or not compiled.fullmatch(digits):
                    continue
                seen.add(span)
                if rule.groupings and not _in_groups(text[span[0] : span[1]], rule):
                    continue
                if is_valid(rule.validator, digits):
                    yield _to_span(rule, span, digits), True


def _in_groups(written: str, rule: PatternRule) -> bool:
    """Whether a number found by joining gaps was written in one of the rule's
    groupings. "98765 43210 102" is 5-5-3, which no card is written as."""

    sizes = [len(group) for group in _DIGITS.findall(written)]
    return sizes in rule.groupings


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
    titles = {word.lower() for word in rule.titles}

    for match in announce.finditer(text):
        tokens: list[re.Match[str]] = []
        position = _after_lead(text, match.end())
        title = _NAME_TOKEN.match(text, position)
        if title is not None and title.group(1).lower().rstrip(".") in titles:
            position = title.end() + (text[title.end() : title.end() + 1] == ".")
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


def _after_lead(text: str, position: int) -> int:
    """Past a colon or dash between the phrase and the name ("my name is:")."""

    lead = _LEAD.match(text, position)
    return lead.end() if lead else position


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
