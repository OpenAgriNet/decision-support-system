"""Tier 2 — the real app writes one access line per turn, with the turn's ids."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from dss.config.settings import Settings
from dss.entrypoint.app import _configure_logging, build_app
from dss.observability.log_fields import KeyValueFormatter, TurnIdsFilter
from tests.integration.entrypoint.test_turn_failures import ExplodingRunner

JSON = "application/json"
SSE = "text/event-stream"


@pytest.mark.parametrize("accept", [JSON, SSE])
def test_a_turn_logs_one_access_line_with_its_sender(a_body, caplog, accept):
    client = TestClient(
        build_app(runner=ExplodingRunner(after=99), settings=Settings())
    )
    body = a_body(context__senderId="farmer-app")

    with caplog.at_level(logging.INFO, logger="dss.http"):
        client.post("/v1/turns", json=body, headers={"Accept": accept})

    [line] = [r for r in caplog.records if r.name == "dss.http"]
    assert line.__dict__["http.response.status_code"] == 200
    assert line.sender_id == "farmer-app"
    assert line.transaction_id == body["context"]["transactionId"]


def test_stderr_prints_the_turns_ids_as_fields():
    dss_logger = logging.getLogger("dss")
    handlers, propagate, level = (
        list(dss_logger.handlers),
        dss_logger.propagate,
        dss_logger.level,
    )
    try:
        _configure_logging()
        [handler] = [h for h in dss_logger.handlers if getattr(h, "_dss_handler", 0)]
    finally:
        dss_logger.handlers[:] = handlers
        dss_logger.propagate, dss_logger.level = propagate, level

    assert isinstance(handler.formatter, KeyValueFormatter)
    assert any(isinstance(f, TurnIdsFilter) for f in handler.filters)
