"""A turn's texts, taken out for redaction and put back.

Redaction works on a flat list of texts so one value gets one tag wherever it
appears. The order is fixed: the question, the enriched question, every history
message — both roles, since an assistant can echo a number back — then the
user's phone, when the turn carries one.
"""

from __future__ import annotations

from collections.abc import Sequence

from dss.core.shared.models import UserTurn


def texts_of(turn: UserTurn) -> list[str]:
    texts = [
        turn.original_query,
        turn.enriched_query,
        *(message.text for message in turn.history),
    ]
    if turn.user.phone:
        texts.append(turn.user.phone)
    return texts


def with_texts(turn: UserTurn, texts: Sequence[str]) -> UserTurn:
    """``turn`` with ``texts`` in place, in the order ``texts_of`` gave them."""

    expected = len(texts_of(turn))
    if len(texts) != expected:
        raise ValueError(f"expected {expected} texts for this turn, got {len(texts)}")
    query, enriched, *rest = texts
    history = rest[: len(turn.history)]
    update: dict = {
        "original_query": query,
        "enriched_query": enriched,
        "history": [
            message.model_copy(update={"text": text})
            for message, text in zip(turn.history, history, strict=True)
        ],
    }
    if turn.user.phone:
        update["user"] = turn.user.model_copy(update={"phone": rest[-1]})
    return turn.model_copy(update=update)
