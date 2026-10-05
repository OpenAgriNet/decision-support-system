"""Turn a token-classification model's output into name spans.

A model tags each token B-PER (a name starts), I-PER (a name goes on) or
something else. A word can be split into several tokens ("Ram" "##esh"); only
the first token's tag counts, and the span covers the whole word. Plain Python,
so it is tested without a model.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

_BEGIN = "B-PER"
_INSIDE = "I-PER"


@dataclass(frozen=True, slots=True)
class Token:
    label: str
    score: float
    start: int  # character offsets into the original text
    end: int
    word: int | None  # which word this token belongs to; None for [CLS]/[SEP]


@dataclass(slots=True)
class _Word:
    label: str
    score: float
    start: int
    end: int


def decode_name_spans(
    tokens: Sequence[Token], *, min_score: float
) -> list[tuple[int, int, float]]:
    """``(start, end, score)`` for every name whose mean word score reaches
    ``min_score``."""

    words: dict[int, _Word] = {}
    for token in tokens:
        if token.word is None:
            continue
        word = words.get(token.word)
        if word is None:
            words[token.word] = _Word(token.label, token.score, token.start, token.end)
        else:
            word.end = max(word.end, token.end)

    spans: list[tuple[int, int, float]] = []
    current: list[_Word] = []

    def close() -> None:
        if current:
            score = round(sum(w.score for w in current) / len(current), 4)
            if score >= min_score:
                spans.append((current[0].start, current[-1].end, score))
            current.clear()

    for word in words.values():
        if word.label == _BEGIN or (word.label == _INSIDE and not current):
            close()
            current.append(word)
        elif word.label == _INSIDE:
            current.append(word)
        else:
            close()
    close()
    return spans
