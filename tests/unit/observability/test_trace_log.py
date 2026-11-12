"""Trace lines: fields as attributes, and request bodies kept off INFO.

A request body carries the farmer's query verbatim, so the level it lands at is
the behaviour worth pinning, not an implementation detail.
"""

from __future__ import annotations

import contextvars
import logging

from dss.observability.trace_log import (
    bind_turn_ids,
    log_event,
    log_external_request,
    trace_component,
)


def test_the_body_is_not_logged_at_info(caplog):
    """§6.2 puts redaction at the sink, but that interceptor does not exist
    yet — so INFO must carry the shape of the call and none of its words."""

    with caplog.at_level(logging.INFO, logger="dss.trace"):
        log_external_request(
            "discovery",
            "txn-1",
            endpoint="http://localhost:8000/discover",
            body={"message": {"intent": "when to sow tomato"}},
        )

    (record,) = caplog.records
    assert "when to sow tomato" not in str(vars(record))
    assert record.external == "discovery"
    assert record.event == "request"


def test_the_body_is_logged_at_debug(caplog):
    """Lowering `dss.trace` to DEBUG is what makes the words visible. Without
    this, dropping the body entirely would still pass the test above."""

    with caplog.at_level(logging.DEBUG, logger="dss.trace"):
        log_external_request(
            "discovery",
            "txn-1",
            body={"message": {"intent": "when to sow tomato"}},
        )

    body_record = caplog.records[-1]
    assert body_record.levelno == logging.DEBUG
    assert body_record.event == "request_body"
    assert "when to sow tomato" in body_record.body


def test_fields_are_attributes_not_message_text(caplog):
    # A log search filters on attributes; text inside the message is not one.
    with caplog.at_level(logging.INFO, logger="dss.trace"):
        log_event("discovery", "txn-1", event="ask_routed", ask_index=0)

    (record,) = caplog.records
    assert record.getMessage() == "discovery ask_routed"
    assert record.request_id == "txn-1"
    assert record.component == "discovery"
    assert record.ask_index == 0


def test_absent_turn_ids_are_left_out(caplog):
    # A `-` attribute matches nothing useful and clutters every filter list.
    # A fresh context, so ids bound by an earlier test cannot leak in.
    def log_outside_a_turn() -> None:
        bind_turn_ids("txn-1")
        log_event("discovery", event="ask_routed")

    with caplog.at_level(logging.INFO, logger="dss.trace"):
        contextvars.Context().run(log_outside_a_turn)

    (record,) = caplog.records
    assert record.request_id == "txn-1"
    assert not hasattr(record, "message_id")
    assert not hasattr(record, "session_id")


def test_exit_carries_elapsed_time_as_a_number(caplog):
    # A number, so a log search can filter `elapsed_ms > 1000`.
    with caplog.at_level(logging.INFO, logger="dss.trace"), trace_component("intent"):
        pass

    exit_record = caplog.records[-1]
    assert exit_record.event == "exit"
    assert exit_record.status == "ok"
    assert isinstance(exit_record.elapsed_ms, float)


def test_non_primitive_values_become_text(caplog):
    # OpenTelemetry drops an attribute it cannot store, so it would vanish.
    with caplog.at_level(logging.INFO, logger="dss.trace"):
        log_event("discovery", event="ask_routed", capabilities=("Weather",))

    (record,) = caplog.records
    assert record.capabilities == "('Weather',)"
