"""The outbound half of the external-call log.

`log_external_response` has always logged what came back. These cover the
request side, whose body carries the farmer's query verbatim — so the level it
lands at is the behaviour worth pinning, not an implementation detail.
"""

from __future__ import annotations

import logging

from dss.observability.trace_log import log_external_request


def test_the_body_is_not_logged_at_info(caplog):
    """A request body holds the farmer's query. §6.2 puts redaction at the
    sink, but that interceptor does not exist yet — so INFO must carry the
    shape of the call and none of its words."""

    with caplog.at_level(logging.INFO, logger="dss.trace"):
        log_external_request(
            "discovery",
            "txn-1",
            endpoint="http://localhost:8000/discover",
            body={"message": {"intent": "when to sow tomato"}},
        )

    logged = caplog.text
    assert "when to sow tomato" not in logged
    assert "external=discovery" in logged
    assert "event=request" in logged


def test_the_body_is_logged_at_debug(caplog):
    """The other half: lowering `dss.trace` to DEBUG is what makes the words
    visible. Without this, dropping the body entirely would still pass the
    test above."""

    with caplog.at_level(logging.DEBUG, logger="dss.trace"):
        log_external_request(
            "discovery",
            "txn-1",
            body={"message": {"intent": "when to sow tomato"}},
        )

    assert "when to sow tomato" in caplog.text
    assert "event=request_body" in caplog.text
