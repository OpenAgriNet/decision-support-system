"""Every regex rule, run over one text.

A pattern runs over both the original text and the shadow copy. The shadow
catches ``98765 43210``; the original catches a phone that the shadow has glued
to the next number (``9876543210 2 acre`` → ``98765432102``).
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from dss.adapters.pii_identifier.regex.models import (
    DeclaringPhraseRule,
    Normalise,
    PatternRule,
    RegexSettings,
)
from dss.adapters.pii_identifier.regex.validators import is_valid
from dss.core.redaction.models import PiiSpan
from dss.core.redaction.normalise import Shadow, shadow

_SOURCE = "regex"

# One word after a declaring phrase: letters, with an inner ' . or - (O'Neil).
_NAME_TOKEN = re.compile(r"[ \t]+([^\W\d_]+(?:['.-][^\W\d_]+)*)")


def identify(text: str, settings: RegexSettings) -> list[PiiSpan]:
    """Every span a rule finds in ``text``, in rule order. Overlaps are left for
    core's ``resolve`` to settle."""

    joined = _shadow(text, settings.normalise)
    found: list[PiiSpan] = []
    for rule in settings.rules:
        if isinstance(rule, PatternRule):
            found.extend(_pattern(rule, text, joined, settings.normalise))
        else:
            found.extend(_declared(rule, text))
    return found


def _pattern(
    rule: PatternRule, text: str, joined: Shadow, normalise: Normalise
) -> Iterator[PiiSpan]:
    compiled = re.compile(rule.pattern)
    seen: set[tuple[int, int]] = set()

    for match in compiled.finditer(text):
        span = match.span()
        if span[0] == span[1]:
            continue
        seen.add(span)
        # Kept in the one form a provider is sent: the gaps joined.
        canonical = _shadow(match.group(), normalise).text
        if is_valid(rule.validator, canonical):
            yield _candidate(rule, span, canonical)

    if joined.text == text:
        return
    for match in compiled.finditer(joined.text):
        if match.start() == match.end():
            continue
        span = joined.to_original(match.start(), match.end())
        if span in seen:
            continue
        seen.add(span)
        if is_valid(rule.validator, match.group()):
            yield _candidate(rule, span, match.group())


def _declared(rule: DeclaringPhraseRule, text: str) -> Iterator[PiiSpan]:
    phrases = sorted(rule.phrases, key=len, reverse=True)
    announce = re.compile(
        r"(?<!\w)(?:" + "|".join(re.escape(p) for p in phrases) + r")(?!\w)",
        re.IGNORECASE,
    )
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
            yield _candidate(rule, span, name)


def _shadow(text: str, normalise: Normalise) -> Shadow:
    return shadow(
        text,
        separators="".join(normalise.join_separators),
        max_separators=normalise.max_separators,
    )


def _candidate(
    rule: PatternRule | DeclaringPhraseRule, span: tuple[int, int], value: str
) -> PiiSpan:
    return PiiSpan(
        start=span[0],
        end=span[1],
        entity=rule.entity,
        score=1.0,
        source=_SOURCE,
        value=value,
    )
