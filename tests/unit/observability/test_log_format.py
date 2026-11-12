from __future__ import annotations

import logging

from dss.observability.log_format import KeyValueFormatter


def _format(message: str, **attributes: object) -> str:
    record = logging.makeLogRecord({"msg": message, **attributes})
    return KeyValueFormatter("%(message)s").format(record)


def test_attributes_follow_the_message_as_key_value() -> None:
    line = _format("discovery exit", request_id="txn-1", status="ok")

    assert line.startswith("discovery exit request_id=txn-1 status=ok")


def test_span_ids_render_as_dash_outside_a_span() -> None:
    line = _format("discovery exit")

    assert line == "discovery exit trace_id=- span_id=-"
