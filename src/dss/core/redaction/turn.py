"""A turn's texts, taken out for redaction and put back.

Redaction works on a flat list of texts so one value gets one tag wherever it
appears. The order is fixed: the question, the enriched question, then every
history message — both roles, since an assistant can echo a number back.
"""

from __future__ import annotations

from collections.abc import Sequence

from dss.core.shared.models import UserTurn


def texts_of(turn: UserTurn) -> list[str]:
    return [
        turn.original_query,
        turn.enriched_query,
        *(message.text for message in turn.history),
    ]


def with_texts(turn: UserTurn, texts: Sequence[str]) -> UserTurn:
    """``turn`` with ``texts`` in place, in the order ``texts_of`` gave them."""

    if len(texts) != 2 + len(turn.history):
        raise ValueError(
            f"expected {2 + len(turn.history)} texts for this turn, got {len(texts)}"
        )
    query, enriched, *history = texts
    return turn.model_copy(
        update={
            "original_query": query,
            "enriched_query": enriched,
            "history": [
                message.model_copy(update={"text": text})
                for message, text in zip(turn.history, history, strict=True)
            ],
        }
    )
