"""The turn record. Holds farmer content on purpose — it is the audit trail."""

from __future__ import annotations

from dss.adapters.sinks.memory import MemoryTurnSink
from dss.core.shared.models import (
    TextBlock,
    TurnContext,
    TurnFinished,
    TurnOutcome,
    TurnStatus,
)

CTX = TurnContext(trace_id="trc_1", session_id="conv_1", message_id="msg_in")


def test_a_turn_is_recorded_under_its_trace_id(a_turn):
    sink = MemoryTurnSink()
    finished = TurnFinished(
        outcome=TurnOutcome(status=TurnStatus.ANSWERED, confidence=92),
        content=(TextBlock(text="Rs 2,275 per quintal."),),
    )

    sink.opened(CTX, a_turn(query="Wheat price?"))
    sink.closed(CTX, finished)

    record = sink.records["trc_1"]
    assert record.turn is not None
    assert record.turn.query == "Wheat price?"
    assert record.finished is finished


def test_an_unfinished_turn_is_still_recorded(a_turn):
    """A turn that crashed before its terminal event must still leave a record —
    that is the case the audit trail exists for."""

    sink = MemoryTurnSink()

    sink.opened(CTX, a_turn())

    assert sink.records["trc_1"].finished is None
