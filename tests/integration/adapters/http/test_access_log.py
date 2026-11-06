"""Tier 2 — one line per request: what was asked, how it ended, and who asked."""

from __future__ import annotations

import logging

import anyio
import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from dss.adapters.http.access_log import AccessLogMiddleware
from dss.observability.log_fields import MISSING, bind_turn_ids

LOGGER = "dss.http"


def _bind_turn() -> None:
    bind_turn_ids(
        transaction_id="txn-1",
        turn_message_id="msg-1",
        session_id="sess-1",
        sender_id="farmer-app",
    )


async def _streamed_turn():
    _bind_turn()
    yield b"data: done\n\n"


def _app() -> FastAPI:
    app = FastAPI(openapi_url=None)

    @app.get("/openapi.json")
    async def probe(fail: bool = False) -> dict:
        if fail:
            raise RuntimeError("not ready")
        return {}

    @app.post("/v1/turns")
    async def turns() -> StreamingResponse:
        return StreamingResponse(_streamed_turn(), media_type="text/event-stream")

    @app.post("/v1/rejected")
    async def rejected() -> dict:
        from fastapi import HTTPException

        raise HTTPException(status_code=422)

    @app.get("/boom")
    async def boom() -> dict:
        raise RuntimeError("broken")

    app.add_middleware(AccessLogMiddleware)
    return app


def _access_lines(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == LOGGER]


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app(), raise_server_exceptions=False)


def test_a_turn_logs_its_route_status_and_sender(client, caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        client.post("/v1/turns?debug=1", json={})

    [line] = _access_lines(caplog)
    assert line.levelno == logging.INFO
    assert line.__dict__["http.request.method"] == "POST"
    assert line.__dict__["url.path"] == "/v1/turns"
    assert line.__dict__["http.response.status_code"] == 200
    assert line.sender_id == "farmer-app"
    assert line.transaction_id == "txn-1"
    assert line.duration_ms >= 0


def test_the_query_string_is_never_logged(client, caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        client.post("/v1/turns?phone=98450", json={})

    [line] = _access_lines(caplog)
    assert "98450" not in str(vars(line))


def test_a_rejected_request_shows_its_sender_as_missing(client, caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        client.post("/v1/rejected", json={})

    [line] = _access_lines(caplog)
    assert line.__dict__["http.response.status_code"] == 422
    assert line.sender_id == MISSING


def test_the_health_probe_is_not_logged(client, caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        client.get("/openapi.json")

    assert _access_lines(caplog) == []


def test_an_unhandled_error_is_logged_with_its_stack(client, caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        client.get("/boom")

    [line] = _access_lines(caplog)
    assert line.levelno == logging.ERROR
    assert line.exc_info is not None
    assert line.__dict__["http.response.status_code"] == 500


def test_a_failing_health_probe_is_logged(client, caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        client.get("/openapi.json?fail=true")

    [line] = _access_lines(caplog)
    assert line.levelno == logging.ERROR


async def test_a_client_that_left_is_logged_as_cancelled(caplog):
    async def cancelled_app(scope, receive, send):
        raise anyio.get_cancelled_exc_class()

    middleware = AccessLogMiddleware(cancelled_app)
    scope = {"type": "http", "method": "POST", "path": "/v1/turns"}

    with (
        caplog.at_level(logging.INFO, logger=LOGGER),
        pytest.raises(anyio.get_cancelled_exc_class()),
    ):
        await middleware(scope, None, None)

    [line] = _access_lines(caplog)
    assert line.levelno == logging.INFO
    assert line.__dict__["http.response.status_code"] == 499
