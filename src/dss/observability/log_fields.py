"""The fields a `dss` log line carries, and how they print on stderr.

- Fields ride on the record, not in the text: the collector exports them as
  attributes, which HyperDX filters on. Text can only be searched.
- A turn's ids are attached once, by a filter, so no caller passes them.
- Stdlib only, like the rest of this package — safe to import from any layer.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import asdict, dataclass

# Shown, not left out, so a filter on a missing id still finds the line.
MISSING = "missing"


@dataclass(frozen=True)
class TurnIds:
    transaction_id: str = MISSING
    turn_message_id: str = MISSING
    session_id: str = MISSING
    sender_id: str = MISSING


_NO_TURN = TurnIds()


@dataclass
class RequestTurnIds:
    """The ids of the turn a request ran, read once the request is done.

    Mutable on purpose: a streamed turn binds its ids in a child task, and only
    a shared object carries them back to the request around it.
    """

    ids: TurnIds = _NO_TURN


_turn_ids: ContextVar[TurnIds] = ContextVar("dss_turn_ids", default=_NO_TURN)
_request_turn_ids: ContextVar[RequestTurnIds | None] = ContextVar(
    "dss_request_turn_ids", default=None
)


def watch_request_turn_ids() -> RequestTurnIds:
    """Start collecting the ids of the turn this request will run."""

    request_turn_ids = RequestTurnIds()
    _request_turn_ids.set(request_turn_ids)
    return request_turn_ids


def bind_turn_ids(
    *, transaction_id: str, turn_message_id: str, session_id: str, sender_id: str
) -> None:
    """Tag every later line in this context, and its child tasks, with the turn.

    Set, never mutated: concurrent turns that share a parent context would
    otherwise overwrite each other's ids.
    """

    ids = TurnIds(
        transaction_id=transaction_id,
        turn_message_id=turn_message_id,
        session_id=session_id,
        sender_id=sender_id,
    )
    _turn_ids.set(ids)
    request_turn_ids = _request_turn_ids.get()
    if request_turn_ids is not None:
        request_turn_ids.ids = ids


def current_turn_ids() -> TurnIds:
    return _turn_ids.get()


class TurnIdsFilter(logging.Filter):
    """Adds the turn's ids to each record, keeping any the caller passed."""

    def filter(self, record: logging.LogRecord) -> bool:
        for name, value in asdict(current_turn_ids()).items():
            if not hasattr(record, name):
                setattr(record, name, value)
        return True


# What every record has. Anything else was added by a caller or a filter.
_STANDARD_ATTRIBUTES = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", None, None))
) | {"message", "asctime"}


class KeyValueFormatter(logging.Formatter):
    """The message, then each field as `key=value`, so a local grep still works."""

    def format(self, record: logging.LogRecord) -> str:
        fields = " ".join(
            f"{name}={_as_text(value)}"
            for name, value in vars(record).items()
            if name not in _STANDARD_ATTRIBUTES
        )
        record.message = record.getMessage()
        if self.usesTime():
            record.asctime = self.formatTime(record, self.datefmt)
        header = self.formatMessage(record)
        line = f"{header} {fields}" if fields else header
        # After the fields, so a grep for an id finds the error's first line.
        if record.exc_info:
            line = f"{line}\n{self.formatException(record.exc_info)}"
        return line


def _as_text(value: object) -> str:
    if isinstance(value, (list, tuple)):
        return ",".join(str(item) for item in value)
    return str(value)
