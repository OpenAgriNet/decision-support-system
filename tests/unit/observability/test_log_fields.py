"""Every `dss` line carries the turn's ids as fields HyperDX can filter on."""

from __future__ import annotations

import logging
import sys
from contextvars import Context

import anyio
import pytest

from dss.observability.log_fields import (
    MISSING,
    KeyValueFormatter,
    TurnIdsFilter,
    bind_turn_ids,
    current_turn_ids,
    watch_request_turn_ids,
)


@pytest.fixture
def record() -> logging.LogRecord:
    return logging.LogRecord("dss.test", logging.INFO, __file__, 1, "hello", None, None)


def _bind_turn(transaction_id: str = "txn-1") -> None:
    bind_turn_ids(
        transaction_id=transaction_id,
        turn_message_id="msg-1",
        session_id="sess-1",
        sender_id="farmer-app",
    )


def test_a_line_outside_a_turn_shows_its_ids_as_missing(record):
    Context().run(TurnIdsFilter().filter, record)

    assert record.transaction_id == MISSING
    assert record.sender_id == MISSING


def test_a_line_inside_a_turn_carries_its_ids(record):
    async def logged_inside_a_turn():
        _bind_turn()
        TurnIdsFilter().filter(record)

    anyio.run(logged_inside_a_turn)

    assert record.transaction_id == "txn-1"
    assert record.turn_message_id == "msg-1"
    assert record.session_id == "sess-1"
    assert record.sender_id == "farmer-app"


def test_an_id_the_caller_passed_is_kept(record):
    record.transaction_id = "txn-explicit"

    async def logged_inside_a_turn():
        _bind_turn()
        TurnIdsFilter().filter(record)

    anyio.run(logged_inside_a_turn)

    assert record.transaction_id == "txn-explicit"


def test_the_request_sees_ids_bound_in_a_child_task():
    """A streamed turn binds its ids in a child task; the access line is
    written by the parent once the stream ends."""

    async def request():
        slot = watch_request_turn_ids()
        async with anyio.create_task_group() as group:
            group.start_soon(_bind_turn_async)
        return slot.ids

    seen = anyio.run(request)

    assert seen.sender_id == "farmer-app"
    assert seen.transaction_id == "txn-1"


async def _bind_turn_async() -> None:
    _bind_turn()


def test_concurrent_turns_keep_their_own_ids():
    seen: dict[str, str] = {}

    async def turn(transaction_id: str) -> None:
        _bind_turn(transaction_id)
        await anyio.sleep(0)
        seen[transaction_id] = current_turn_ids().transaction_id

    async def two_turns():
        _bind_turn("txn-parent")
        async with anyio.create_task_group() as group:
            group.start_soon(turn, "txn-a")
            group.start_soon(turn, "txn-b")

    anyio.run(two_turns)

    assert seen == {"txn-a": "txn-a", "txn-b": "txn-b"}


def test_stderr_prints_fields_after_the_message(record):
    record.component = "intent"
    record.event = "enter"

    line = KeyValueFormatter("%(message)s").format(record)

    assert line == "hello component=intent event=enter"


def test_stderr_joins_a_list_field(record):
    record.provider_ids = ["bpp-a", "bpp-b"]

    line = KeyValueFormatter("%(message)s").format(record)

    assert line == "hello provider_ids=bpp-a,bpp-b"


def test_stderr_puts_fields_on_the_error_line_before_the_traceback():
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord(
            "dss.test", logging.ERROR, __file__, 1, "failed", None, sys.exc_info()
        )
    record.transaction_id = "txn-1"

    first, *rest = KeyValueFormatter("%(message)s").format(record).splitlines()

    assert first == "failed transaction_id=txn-1"
    assert "ValueError: boom" in rest[-1]
