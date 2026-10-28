"""A turn's texts, taken out for redaction and put back.

Redaction works on a flat list of texts so one value gets one tag wherever it
appears. The order is fixed: the question, every history message — both roles,
since an assistant can echo a number back — then the user's phone, when the
turn carries one.
"""

from __future__ import annotations

from collections.abc import Sequence

from dss.core.shared.models import TextQuery, TurnInput, UserTurn


def texts_of(turn: UserTurn[TextQuery]) -> list[str]:
    return [turn.input.text, *surrounding_texts_of(turn)]


def surrounding_texts_of(turn: UserTurn[TurnInput]) -> list[str]:
    """The texts every kind of turn carries around its input: the history,
    then the phone."""

    texts = [message.text for message in turn.history]
    if turn.user.phone:
        texts.append(turn.user.phone)
    return texts


def with_texts(turn: UserTurn[TextQuery], texts: Sequence[str]) -> UserTurn[TextQuery]:
    """``turn`` with ``texts`` in place, in the order ``texts_of`` gave them."""

    _expect_count(turn, texts, expected=len(texts_of(turn)))
    query, *rest = texts
    turn = turn.model_copy(
        update={"input": turn.input.model_copy(update={"text": query})}
    )
    return with_surrounding_texts(turn, rest)


def with_surrounding_texts[InputT: TurnInput](
    turn: UserTurn[InputT], texts: Sequence[str]
) -> UserTurn[InputT]:
    """``turn`` with ``texts`` in place, in the order ``surrounding_texts_of``
    gave them."""

    _expect_count(turn, texts, expected=len(surrounding_texts_of(turn)))
    history = texts[: len(turn.history)]
    update: dict = {
        "history": [
            message.model_copy(update={"text": text})
            for message, text in zip(turn.history, history, strict=True)
        ],
    }
    if turn.user.phone:
        update["user"] = turn.user.model_copy(update={"phone": texts[-1]})
    return turn.model_copy(update=update)


def _expect_count(turn: UserTurn, texts: Sequence[str], *, expected: int) -> None:
    if len(texts) != expected:
        raise ValueError(f"expected {expected} texts for this turn, got {len(texts)}")
