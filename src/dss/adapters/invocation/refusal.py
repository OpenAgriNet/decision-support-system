"""What a provider says when it refuses a form call, read from its NACK.

The farmer reads it in place of the DSS's own line, so only a refusal of
their request counts: not an outage, and not a refusal of our credentials,
whose bodies can be stack traces or about us."""

from __future__ import annotations

import unicodedata
from http import HTTPStatus

import httpx
from pydantic import BaseModel, ValidationError

from dss.core.redaction.visibility import Visibility

# Long enough for a sentence; a longer body is a dump, not a reason.
_MAX_MESSAGE_LENGTH = 200

_NOT_THE_FARMERS_REQUEST = frozenset(
    {
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.REQUEST_TIMEOUT,
        HTTPStatus.TOO_MANY_REQUESTS,
    }
)


class _Error(BaseModel):
    message: str


# Beckn's `message` envelope; a NACK carries its error here or beside it.
class _NackEnvelope(BaseModel):
    error: _Error | str | None = None


class _Nack(BaseModel):
    message: _NackEnvelope | str | None = None
    error: _Error | str | None = None


def refusal_message_of(
    response: httpx.Response, *, visibility: Visibility
) -> str | None:
    if not _is_refusal(response.status_code):
        return None
    try:
        nack = _Nack.model_validate_json(response.content)
    except ValidationError:
        return None
    error = _error_in(nack)
    if error is None:
        return None
    # Masked whole, then measured: a cut could leave half a value unmasked.
    message = visibility.conceal(_flatten_to_one_line(error.message))
    if not message or len(message) > _MAX_MESSAGE_LENGTH:
        return None
    return message


def _error_in(nack: _Nack) -> _Error | None:
    inner = nack.message.error if isinstance(nack.message, _NackEnvelope) else None
    for error in (inner, nack.error):
        if isinstance(error, _Error):
            return error
    return None


def _is_refusal(status_code: int) -> bool:
    return (
        HTTPStatus.BAD_REQUEST <= status_code < HTTPStatus.INTERNAL_SERVER_ERROR
        and status_code not in _NOT_THE_FARMERS_REQUEST
    )


# Control characters only: a zero-width joiner is part of a Devanagari word.
def _flatten_to_one_line(text: str) -> str:
    printable = "".join(
        " " if unicodedata.category(char) == "Cc" else char for char in text
    )
    return " ".join(printable.split())
