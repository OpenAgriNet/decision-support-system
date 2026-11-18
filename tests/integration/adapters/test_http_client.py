from __future__ import annotations

import logging
from collections.abc import Callable

import httpx
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from dss.adapters.http_client import HttpClient

BASE_URL = "https://bpp.example/oan"


class _Replies:
    def __init__(self, *status_codes: int) -> None:
        self._status_codes = list(status_codes)
        self.urls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(str(request.url))
        # The last status repeats for every later try.
        status_code = (
            self._status_codes.pop(0)
            if len(self._status_codes) > 1
            else self._status_codes[0]
        )
        return httpx.Response(status_code, json={"echo": "secret-body"})


def _refuse_connection(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


def _retry_any(error: httpx.HTTPError) -> bool:
    return True


def _http(handler: Callable[[httpx.Request], httpx.Response]) -> HttpClient:
    return HttpClient(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        base_url=BASE_URL,
        backoff_seconds=0.0,
    )


@pytest.fixture
def spans(monkeypatch) -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr("opentelemetry.trace.get_tracer_provider", lambda: provider)
    return exporter


async def test_a_relative_path_is_joined_to_the_base_url() -> None:
    replies = _Replies(200)

    response = await _http(replies).post("support", json={"a": 1})

    assert response.status_code == 200
    assert replies.urls == [f"{BASE_URL}/support"]


async def test_a_non_2xx_raises_the_status_error() -> None:
    with pytest.raises(httpx.HTTPStatusError) as failed:
        await _http(_Replies(404)).post("support", json={})

    assert failed.value.response.status_code == 404


async def test_no_reply_raises_the_transport_error_unwrapped() -> None:
    with pytest.raises(httpx.ConnectError):
        await _http(_refuse_connection).post("support", json={})


async def test_no_retries_sends_once() -> None:
    replies = _Replies(503)

    with pytest.raises(httpx.HTTPStatusError):
        await _http(replies).post("support", json={})

    assert len(replies.urls) == 1


async def test_a_retryable_failure_is_tried_again_until_it_succeeds() -> None:
    replies = _Replies(503, 503, 200)

    response = await _http(replies).post(
        "support", json={}, retries=2, should_retry=_retry_any
    )

    assert response.status_code == 200
    assert len(replies.urls) == 3


async def test_retries_stop_at_the_given_count() -> None:
    replies = _Replies(503)

    with pytest.raises(httpx.HTTPStatusError):
        await _http(replies).post(
            "support", json={}, retries=2, should_retry=_retry_any
        )

    assert len(replies.urls) == 3


async def test_a_transport_error_is_retried_too() -> None:
    calls: list[httpx.Request] = []

    def refuse(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return _refuse_connection(request)

    with pytest.raises(httpx.ConnectError):
        await _http(refuse).post("support", json={}, retries=1, should_retry=_retry_any)

    assert len(calls) == 2


async def test_retries_without_a_rule_send_once() -> None:
    replies = _Replies(503)

    with pytest.raises(httpx.HTTPStatusError):
        await _http(replies).post("support", json={}, retries=2)

    assert len(replies.urls) == 1


async def test_every_try_sends_the_same_body() -> None:
    bodies: list[bytes] = []

    def unavailable(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        return httpx.Response(503)

    with pytest.raises(httpx.HTTPStatusError):
        await _http(unavailable).post(
            "support", json={"id": "m1"}, retries=2, should_retry=_retry_any
        )

    assert len(bodies) == 3
    assert len(set(bodies)) == 1


async def test_the_wait_between_tries_doubles(monkeypatch) -> None:
    waits: list[float] = []

    async def record_wait(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr("dss.adapters.http_client.anyio.sleep", record_wait)
    http = HttpClient(
        httpx.AsyncClient(transport=httpx.MockTransport(_Replies(503))),
        base_url=BASE_URL,
        backoff_seconds=0.5,
    )

    with pytest.raises(httpx.HTTPStatusError):
        await http.post("support", json={}, retries=2, should_retry=_retry_any)

    assert waits == [0.5, 1.0]


async def test_a_failure_should_retry_turns_down_is_not_retried() -> None:
    replies = _Replies(503)

    with pytest.raises(httpx.HTTPStatusError):
        await _http(replies).post(
            "support", json={}, retries=2, should_retry=lambda error: False
        )

    assert len(replies.urls) == 1


async def test_each_try_gets_a_span_with_its_status_code(spans) -> None:
    with pytest.raises(httpx.HTTPStatusError):
        await _http(_Replies(503, 429, 500)).post(
            "support", json={}, retries=2, should_retry=_retry_any
        )

    tries = [s for s in spans.get_finished_spans() if s.name == "dss.http.post"]
    assert [s.attributes["http.status_code"] for s in tries] == [503, 429, 500]
    assert "secret-body" not in str([dict(s.attributes) for s in tries])


async def test_a_try_with_no_reply_has_no_status_code(spans) -> None:
    with pytest.raises(httpx.ConnectError):
        await _http(_refuse_connection).post("support", json={})

    (only_try,) = [s for s in spans.get_finished_spans() if s.name == "dss.http.post"]
    assert "http.status_code" not in only_try.attributes


async def test_each_retry_is_logged_once_without_the_body(caplog) -> None:
    with (
        caplog.at_level(logging.INFO, logger="dss.trace"),
        pytest.raises(httpx.HTTPStatusError),
    ):
        await _http(_Replies(503, 429, 500)).post(
            "support",
            json={"farmer": "secret-request"},
            retries=2,
            should_retry=_retry_any,
        )

    retries = [r for r in caplog.records if getattr(r, "event", "") == "retry"]
    assert [(r.method, r.path, r.status, r.attempt) for r in retries] == [
        ("POST", "support", 503, 1),
        ("POST", "support", 429, 2),
    ]
    assert "secret" not in caplog.text


async def test_a_retried_transport_error_is_logged_by_its_type(caplog) -> None:
    with (
        caplog.at_level(logging.INFO, logger="dss.trace"),
        pytest.raises(httpx.ConnectError),
    ):
        await _http(_refuse_connection).post(
            "support", json={}, retries=1, should_retry=_retry_any
        )

    (retry,) = [r for r in caplog.records if getattr(r, "event", "") == "retry"]
    assert retry.status == "ConnectError"
