"""Redact a turn's texts in one pass: detect → resolve → replace.

The question and every history message go through together, so a number the
farmer typed twice gets one tag, «phone_1», wherever it appears. The result holds
the redacted texts, the ``RevealMap`` for the provider call, and how many of each
entity were found — counts only, never values, so they are safe to send to
telemetry.

``extra`` is where candidates from another detector join the pass — one list per
text, in the same order. #136 feeds a model's names in here.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from dss.core.redaction.detect import detect
from dss.core.redaction.models import Candidate, RedactionConfig
from dss.core.redaction.resolve import resolve
from dss.core.redaction.reveal import RevealMap

_GAPS = re.compile(r"[\s-]+")


@dataclass(frozen=True, slots=True)
class Redaction:
    texts: tuple[str, ...]
    reveal: RevealMap
    found: dict[str, int]  # entity → how many spans were replaced


def redact(
    texts: Sequence[str],
    config: RedactionConfig,
    extra: Sequence[Sequence[Candidate]] = (),
) -> Redaction:
    tags: dict[tuple[str, str], str] = {}
    numbers: dict[str, int] = {}
    values: dict[str, str] = {}
    found: dict[str, int] = {}
    redacted: list[str] = []

    for i, text in enumerate(texts):
        candidates = detect(text, config)
        if i < len(extra):
            candidates.extend(extra[i])

        out: list[str] = []
        last = 0
        for candidate in resolve(candidates):
            # The same value, however it was spaced, gets the same tag.
            key = (candidate.entity, _same_value(candidate, text))
            tag = tags.get(key)
            if tag is None:
                numbers[candidate.entity] = numbers.get(candidate.entity, 0) + 1
                tag = f"«{candidate.entity}_{numbers[candidate.entity]}»"
                tags[key] = tag
                if candidate.value is not None:
                    values[tag] = candidate.value
            found[candidate.entity] = found.get(candidate.entity, 0) + 1
            out.append(text[last : candidate.start])
            out.append(tag)
            last = candidate.end
        out.append(text[last:])
        redacted.append("".join(out))

    return Redaction(
        texts=tuple(redacted),
        reveal=RevealMap(values=values, normalise=config.normalise),
        found=found,
    )


def _same_value(candidate: Candidate, text: str) -> str:
    if candidate.value is not None:
        return candidate.value.lower()
    return _GAPS.sub("", text[candidate.start : candidate.end]).lower()
