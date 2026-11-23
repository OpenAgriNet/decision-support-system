from __future__ import annotations

from http import HTTPStatus

import httpx

_NOT_TAKEN_IN_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
_NOT_TAKEN_IN_STATUSES = frozenset(
    {HTTPStatus.REQUEST_TIMEOUT, HTTPStatus.TOO_MANY_REQUESTS}
)


def was_not_taken_in(error: httpx.HTTPError) -> bool:
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in _NOT_TAKEN_IN_STATUSES
    return isinstance(error, _NOT_TAKEN_IN_ERRORS)
