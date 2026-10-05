"""Redact a turn's texts from the spans identifiers found: resolve → replace.

How the spans were found is not this module's business — regex, a model, an
HTTP service, or several at once (``ports/pii_identifier.py``). This is the part
that stays the same whichever identifiers run:

- overlapping spans are settled (``resolve``);
- each span becomes a numbered tag, «phone_1»; the same value, in any of the
  texts, gets the same tag;
- the policy decides which real values are held in the ``RevealMap`` for the
  provider call, and which are destroyed.

The result carries counts per entity — never values — so they are safe to send
to telemetry. No spans means the texts come back unchanged, which is what a
turn gets when redaction is off.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from dss.core.redaction.models import PiiSpan, RedactionPolicy
from dss.core.redaction.normalise import join_number_gaps
from dss.core.redaction.resolve import resolve
from dss.core.redaction.reveal import RevealMap


@dataclass(frozen=True, slots=True)
class Redaction:
    texts: tuple[str, ...]
    reveal: RevealMap
    found: dict[str, int]  # entity → how many spans were replaced
    # Identifiers that failed on this turn; the others' spans were still applied.
    failed: tuple[str, ...] = ()


def redact(
    texts: Sequence[str],
    spans: Sequence[Sequence[PiiSpan]],
    policy: RedactionPolicy,
) -> Redaction:
    """``spans[i]`` are the spans found in ``texts[i]``, from every identifier.
    A missing entry means none."""

    tags: dict[tuple[str, str], str] = {}
    numbers: dict[str, int] = {}
    values: dict[str, str] = {}
    found: dict[str, int] = {}
    redacted: list[str] = []

    for i, text in enumerate(texts):
        out: list[str] = []
        last = 0
        for span in resolve(spans[i] if i < len(spans) else ()):
            # The same value, however it was spaced, gets the same tag.
            key = (span.entity, join_number_gaps(span.value).text.lower())
            tag = tags.get(key)
            if tag is None:
                numbers[span.entity] = numbers.get(span.entity, 0) + 1
                tag = f"«{span.entity}_{numbers[span.entity]}»"
                tags[key] = tag
                if policy.keeps(span.entity):
                    values[tag] = span.value
            found[span.entity] = found.get(span.entity, 0) + 1
            out.append(text[last : span.start])
            out.append(tag)
            last = span.end
        out.append(text[last:])
        redacted.append("".join(out))

    return Redaction(
        texts=tuple(redacted), reveal=RevealMap(values=values), found=found
    )
