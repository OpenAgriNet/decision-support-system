"""A turn's texts, taken out for redaction and put back.

Redaction works on a flat list of texts so one value gets one tag wherever it
appears. The order is fixed: the question, every history message — both roles,
since an assistant can echo a number back — then the user's phone, when the
turn carries one.
"""

from __future__ import annotations

from collections.abc import Sequence

from dss.core.shared.models import TextQuery, UserTurn


def texts_of(turn: UserTurn[TextQuery]) -> list[str]:
    texts = [turn.input.text, *(message.text for message in turn.history)]
    if turn.user.phone:
        texts.append(turn.user.phone)
    return texts


def with_texts(turn: UserTurn[TextQuery], texts: Sequence[str]) -> UserTurn[TextQuery]:
    """``turn`` with ``texts`` in place, in the order ``texts_of`` gave them."""

    expected = len(texts_of(turn))
    if len(texts) != expected:
        raise ValueError(f"expected {expected} texts for this turn, got {len(texts)}")
    query, *rest = texts
    history = rest[: len(turn.history)]
    update: dict = {
        "input": turn.input.model_copy(update={"text": query}),
        "history": [
            message.model_copy(update={"text": text})
            for message, text in zip(turn.history, history, strict=True)
        ],
    }
    if turn.user.phone:
        update["user"] = turn.user.model_copy(update={"phone": rest[-1]})
    return turn.model_copy(update=update)
