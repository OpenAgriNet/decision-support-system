from __future__ import annotations

import httpx
import pytest

from dss.adapters.invocation.failures import was_not_taken_in

_REQUEST = httpx.Request("POST", "https://provider.example/support")


def _status_error(status_code: int) -> httpx.HTTPStatusError:
    response = httpx.Response(status_code, request=_REQUEST)
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
