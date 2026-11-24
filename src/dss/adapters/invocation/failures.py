from __future__ import annotations

from http import HTTPStatus

import httpx

from dss.core.shared.models import ProviderFailureKind

_NOT_TAKEN_IN_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
_NOT_TAKEN_IN_STATUSES = frozenset(
    {HTTPStatus.REQUEST_TIMEOUT, HTTPStatus.TOO_MANY_REQUESTS}
)
_UNAUTHORISED_STATUSES = frozenset({HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN})


def was_not_taken_in(error: httpx.HTTPError) -> bool:
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in _NOT_TAKEN_IN_STATUSES
    return isinstance(error, _NOT_TAKEN_IN_ERRORS)


def kind_of(error: httpx.HTTPError) -> ProviderFailureKind:
    if was_not_taken_in(error):
        return ProviderFailureKind.NOT_SENT
    if isinstance(error, httpx.HTTPStatusError):
        return _kind_of_status(error.response.status_code)
    return ProviderFailureKind.OUTCOME_UNKNOWN


def _kind_of_status(status_code: int) -> ProviderFailureKind:
    if status_code in _UNAUTHORISED_STATUSES:
        return ProviderFailureKind.UNAUTHORISED
    if _is_client_error(status_code):
        return ProviderFailureKind.REFUSED
    # A redirect on a POST is a misconfiguration; like a 5xx, it may have filed.
    return ProviderFailureKind.OUTCOME_UNKNOWN


def _is_client_error(status_code: int) -> bool:
    return HTTPStatus.BAD_REQUEST <= status_code < HTTPStatus.INTERNAL_SERVER_ERROR


# Not masked here: the caller holds the visibility that conceals it.
def reason_of(error: httpx.HTTPError) -> str | None:
    if not isinstance(error, httpx.HTTPStatusError):
        return None
    body = error.response.text
    return body if body.strip() else None
