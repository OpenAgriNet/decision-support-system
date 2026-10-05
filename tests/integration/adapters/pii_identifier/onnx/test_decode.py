"""Tier 2 — turning a token-classification model's output into name spans.

No model here: the inputs are what a tokenizer and a model would hand back —
one label and score per token, the token's character span, and which word it
belongs to (``None`` for the special tokens a BERT tokenizer adds).
"""

from __future__ import annotations

from dss.adapters.pii_identifier.onnx.decode import Token, decode_name_spans


def tok(label: str, start: int, end: int, word: int | None, score: float = 0.99):
    return Token(label=label, score=score, start=start, end=end, word=word)


CLS = tok("O", 0, 0, None)
SEP = tok("O", 0, 0, None)


def test_a_two_word_name() -> None:
    # "my name is Ramesh Patil"
    tokens = [
        CLS,
        tok("O", 0, 2, 0),
        tok("O", 3, 7, 1),
        tok("O", 8, 10, 2),
        tok("B-PER", 11, 17, 3),
        tok("I-PER", 18, 23, 4),
        SEP,
    ]
    assert decode_name_spans(tokens, min_score=0.5) == [(11, 23, 0.99)]


def test_word_pieces_join_into_one_word() -> None:
    # "Rameshwar" split as "Ram" "##esh" "##war": only the first piece's label
    # counts, and the span covers all three.
    tokens = [
        CLS,
        tok("B-PER", 0, 3, 0),
        tok("O", 3, 6, 0),
        tok("I-ORG", 6, 9, 0),
        tok("O", 10, 13, 1),
        SEP,
    ]
    assert decode_name_spans(tokens, min_score=0.5) == [(0, 9, 0.99)]


def test_two_names_side_by_side_stay_apart() -> None:
    # "Ramesh Suresh" both tagged B-PER: two people, not one.
    tokens = [tok("B-PER", 0, 6, 0), tok("B-PER", 7, 13, 1)]
    assert decode_name_spans(tokens, min_score=0.5) == [(0, 6, 0.99), (7, 13, 0.99)]


def test_an_inside_tag_with_nothing_before_it_starts_a_name() -> None:
    tokens = [tok("O", 0, 3, 0), tok("I-PER", 4, 10, 1)]
    assert decode_name_spans(tokens, min_score=0.5) == [(4, 10, 0.99)]


def test_other_entity_types_are_ignored() -> None:
    tokens = [tok("B-LOC", 0, 4, 0), tok("B-ORG", 5, 9, 1)]
    assert decode_name_spans(tokens, min_score=0.5) == []


def test_a_low_score_name_is_left_alone() -> None:
    tokens = [tok("B-PER", 0, 6, 0, score=0.9), tok("I-PER", 7, 12, 1, score=0.5)]
    # Mean 0.7: kept at 0.6, dropped at 0.8.
    assert decode_name_spans(tokens, min_score=0.6) == [(0, 12, 0.7)]
    assert decode_name_spans(tokens, min_score=0.8) == []


def test_no_tokens() -> None:
    assert decode_name_spans([], min_score=0.5) == []
