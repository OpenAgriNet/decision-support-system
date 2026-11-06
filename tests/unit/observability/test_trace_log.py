"""Trace lines carry their facts as record fields, and keep farmer words at DEBUG."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pytest

from dss.observability.trace_log import (
    log_external_request,
    log_external_response,
    log_failure,
    trace_agent_call,
    trace_component,
)


def _records(caplog, **match):
    return [
        r
        for r in caplog.records
        if all(getattr(r, key, None) == value for key, value in match.items())
    ]


def test_the_body_is_not_logged_at_info(caplog):
    """A request body holds the farmer's query; INFO carries only its shape."""

    with caplog.at_level(logging.INFO, logger="dss.trace"):
        log_external_request(
            "discovery",
            "txn-1",
            endpoint="http://localhost:8000/discover",
            body={"message": {"intent": "when to sow tomato"}},
        )

    assert "when to sow tomato" not in caplog.text
    [line] = _records(caplog, external="discovery", event="request")
    assert line.transaction_id == "txn-1"
    assert line.endpoint == "http://localhost:8000/discover"


def test_the_body_is_logged_at_debug(caplog):
    with caplog.at_level(logging.DEBUG, logger="dss.trace"):
        log_external_request(
            "discovery",
            "txn-1",
            body={"message": {"intent": "when to sow tomato"}},
        )

    [line] = _records(caplog, event="request_body")
    assert line.levelno == logging.DEBUG
    assert "when to sow tomato" in line.body


def test_a_response_line_carries_its_status_and_fields(caplog):
    with caplog.at_level(logging.INFO, logger="dss.trace"):
        log_external_response(
            "invocation", "txn-1", status=200, provider_id="bpp-a", body="{}"
        )

    [line] = _records(caplog, external="invocation", event="response")
    assert line.status == 200
    assert line.provider_id == "bpp-a"


def test_a_component_logs_enter_and_exit(caplog):
    with (
        caplog.at_level(logging.INFO, logger="dss.trace"),
        trace_component("intent", "txn-1"),
    ):
        pass

    [enter] = _records(caplog, component="intent", event="enter")
    [leave] = _records(caplog, component="intent", event="exit")
    assert enter.transaction_id == "txn-1"
    assert leave.status == "ok"
    assert leave.elapsed_ms >= 0


def test_a_failing_component_exits_with_the_error_type(caplog):
    with (
        caplog.at_level(logging.INFO, logger="dss.trace"),
        pytest.raises(ValueError),
        trace_component("intent", "txn-1"),
    ):
        raise ValueError("boom")

    [leave] = _records(caplog, component="intent", event="exit")
    assert leave.status == "error:ValueError"


@dataclass
class _Usage:
    input_tokens: int = 0
    output_tokens: int = 0


def test_an_agent_call_logs_before_and_after(caplog):
    usage = _Usage()

    with (
        caplog.at_level(logging.INFO, logger="dss.trace"),
        trace_agent_call("intent-classifier", model="gpt-x", usage=usage),
    ):
        usage.input_tokens, usage.output_tokens = 120, 30

    [before] = _records(caplog, agent="intent-classifier", event="agent_call")
    [after] = _records(caplog, agent="intent-classifier", event="agent_done")
    assert before.model == "gpt-x"
    assert after.status == "ok"
    assert (after.input_tokens, after.output_tokens) == (120, 30)
    assert after.elapsed_ms >= 0


def test_a_failing_agent_call_still_logs_after(caplog):
    with (
        caplog.at_level(logging.INFO, logger="dss.trace"),
        pytest.raises(TimeoutError),
        trace_agent_call("composer", model="gpt-x", usage=_Usage()),
    ):
        raise TimeoutError

    [after] = _records(caplog, agent="composer", event="agent_done")
    assert after.status == "error:TimeoutError"


@pytest.mark.parametrize(
    ("status", "level"),
    [
        (200, logging.INFO),
        (503, logging.WARNING),
        ("transport_error", logging.WARNING),
        (None, logging.INFO),
    ],
)
def test_a_failed_response_is_a_warning(caplog, status, level):
    with caplog.at_level(logging.INFO, logger="dss.trace"):
        log_external_response("discovery", "txn-1", status=status)

    [line] = _records(caplog, event="response")
    assert line.levelno == level


def test_a_failing_component_exits_at_warning(caplog):
    with (
        caplog.at_level(logging.INFO, logger="dss.trace"),
        pytest.raises(ValueError),
        trace_component("intent", "txn-1"),
    ):
        raise ValueError

    [enter] = _records(caplog, event="enter")
    [leave] = _records(caplog, event="exit")
    assert (enter.levelno, leave.levelno) == (logging.INFO, logging.WARNING)


def test_a_caught_failure_is_a_warning_with_its_type(caplog):
    with caplog.at_level(logging.INFO, logger="dss.trace"):
        log_failure(
            "area_lookup", event="source_unavailable", error_type="TimeoutError"
        )

    [line] = _records(caplog, event="source_unavailable")
    assert line.levelno == logging.WARNING
    assert line.error_type == "TimeoutError"
    assert line.exc_info is None


def test_a_closed_stream_is_cancelled_not_a_failure(caplog):
    with (
        caplog.at_level(logging.INFO, logger="dss.trace"),
        pytest.raises(GeneratorExit),
        trace_agent_call("composer", model="gpt-x", usage=_Usage()),
    ):
        raise GeneratorExit

    [after] = _records(caplog, event="agent_done")
    assert (after.status, after.levelno) == ("cancelled", logging.INFO)
