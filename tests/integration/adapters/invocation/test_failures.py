from __future__ import annotations

import httpx
import pytest

from dss.adapters.invocation.failures import kind_of, reason_of, was_not_taken_in
from dss.core.shared.models import ProviderFailureKind

_REQUEST = httpx.Request("POST", "https://provider.example/support")


def _status_error(status_code: int, *, body: str = "") -> httpx.HTTPStatusError:
    response = httpx.Response(status_code, text=body, request=_REQUEST)
    return httpx.HTTPStatusError("failed", request=_REQUEST, response=response)


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("refused", request=_REQUEST),
        httpx.ConnectTimeout("timed out", request=_REQUEST),
        httpx.PoolTimeout("no free connection", request=_REQUEST),
        _status_error(408),
        _status_error(429),
    ],
    ids=["connect-error", "connect-timeout", "pool-timeout", "408", "429"],
)
def test_a_request_the_provider_did_not_take_in(error: httpx.HTTPError) -> None:
    assert was_not_taken_in(error)


@pytest.mark.parametrize(
    "error",
    [
        httpx.ReadTimeout("timed out", request=_REQUEST),
        httpx.RemoteProtocolError("dropped", request=_REQUEST),
        _status_error(400),
        _status_error(404),
        _status_error(500),
        _status_error(503),
    ],
    ids=["read-timeout", "remote-protocol-error", "400", "404", "500", "503"],
)
def test_a_request_that_may_have_been_taken_in(error: httpx.HTTPError) -> None:
    assert not was_not_taken_in(error)


@pytest.mark.parametrize(
    ("status_code", "kind"),
    [
        (401, ProviderFailureKind.UNAUTHORISED),
        (403, ProviderFailureKind.UNAUTHORISED),
        (408, ProviderFailureKind.NOT_SENT),
        (429, ProviderFailureKind.NOT_SENT),
        (400, ProviderFailureKind.REFUSED),
        (404, ProviderFailureKind.REFUSED),
        (409, ProviderFailureKind.REFUSED),
        (422, ProviderFailureKind.REFUSED),
        (500, ProviderFailureKind.OUTCOME_UNKNOWN),
        (502, ProviderFailureKind.OUTCOME_UNKNOWN),
        (503, ProviderFailureKind.OUTCOME_UNKNOWN),
        (307, ProviderFailureKind.OUTCOME_UNKNOWN),
    ],
)
def test_the_status_decides_the_kind(
    status_code: int, kind: ProviderFailureKind
) -> None:
    assert kind_of(_status_error(status_code)) is kind


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("refused", request=_REQUEST),
        httpx.ConnectTimeout("timed out", request=_REQUEST),
        httpx.PoolTimeout("no free connection", request=_REQUEST),
    ],
    ids=["connect-error", "connect-timeout", "pool-timeout"],
)
def test_a_request_that_never_left_is_not_sent(error: httpx.HTTPError) -> None:
    assert kind_of(error) is ProviderFailureKind.NOT_SENT


@pytest.mark.parametrize(
    "error",
    [
        httpx.ReadTimeout("timed out", request=_REQUEST),
        httpx.RemoteProtocolError("dropped", request=_REQUEST),
    ],
    ids=["read-timeout", "remote-protocol-error"],
)
def test_a_lost_reply_is_outcome_unknown(error: httpx.HTTPError) -> None:
    assert kind_of(error) is ProviderFailureKind.OUTCOME_UNKNOWN


_PROD_NOT_FOUND = (
    '{"message":{"status":"NACK","error":{"code":"BIZ_GENERIC_ERROR",'
    '"message":"No PMFBY application was found for this application number."}}}'
)


def test_the_reason_is_the_body() -> None:
    assert reason_of(_status_error(404, body=_PROD_NOT_FOUND)) == _PROD_NOT_FOUND


def test_an_html_body_is_its_reason_as_is() -> None:
    page = "<html><body>Bad gateway</body></html>"

    assert reason_of(_status_error(502, body=page)) == page


@pytest.mark.parametrize("body", ["", "  "], ids=["empty", "blank"])
def test_an_empty_body_has_no_reason(body: str) -> None:
    assert reason_of(_status_error(404, body=body)) is None


def test_a_transport_error_has_no_reason() -> None:
    assert reason_of(httpx.ReadTimeout("timed out", request=_REQUEST)) is None
