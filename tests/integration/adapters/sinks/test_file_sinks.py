"""Evidence written to disk as JSON Lines.

A stand-in for the external evidence API. One record per line so a run can be
inspected with `tail`, `grep` and `jq`, and so an appender never has to rewrite
what is already there.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from dss.adapters.sinks.file import FileTelemetrySink, FileTurnSink
from dss.core.shared.models import (
    Source,
    SourceKind,
    TextBlock,
    TurnContext,
    TurnFinished,
    TurnOutcome,
    TurnStatus,
)

NOW = datetime(2026, 9, 4, 8, 0, tzinfo=UTC)
CTX = TurnContext(trace_id="trc_1", session_id="conv_1", message_id="msg_in")


def _lines(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _finished() -> TurnFinished:
    return TurnFinished(
        outcome=TurnOutcome(status=TurnStatus.ANSWERED, confidence=92),
        content=(TextBlock(text="Rs 2,275 per quintal.", source_ids=("src_1",)),),
        sources=(Source(id="src_1", name="Agmarknet", kind=SourceKind.PROVIDER),),
    )


# --- telemetry --------------------------------------------------------------


def test_each_stage_appends_one_line(tmp_path):
    path = tmp_path / "telemetry.jsonl"
    sink = FileTelemetrySink(path, clock=lambda: NOW)

    sink.stage("moderation", CTX, "proceed")
    sink.stage("intent", CTX, "mandi-prices")

    assert [r["stage"] for r in _lines(path)] == ["moderation", "intent"]


def test_a_telemetry_record_carries_the_join_keys_and_a_timestamp(tmp_path):
    path = tmp_path / "telemetry.jsonl"

    FileTelemetrySink(path, clock=lambda: NOW).stage("moderation", CTX, "proceed")

    record = _lines(path)[0]
    assert record["trace_id"] == "trc_1"
    assert record["session_id"] == "conv_1"
    assert record["recorded_at"] == "2026-09-04T08:00:00Z"


def test_no_telemetry_record_carries_farmer_content(tmp_path, a_turn):
    """The metrics tier holds no user content — `DSS_ARCHITECTURE.md` §6.3. This
    file is the one that can be read broadly, so the rule is enforced by the
    port's shape: `stage` is handed a short string, never the turn."""

    path = tmp_path / "telemetry.jsonl"
    query = "my neighbour Ramesh asked about wheat"

    FileTelemetrySink(path, clock=lambda: NOW).stage("intent", CTX, "mandi-prices")

    assert query not in path.read_text()


def test_the_directory_is_created_if_it_is_missing(tmp_path):
    path = tmp_path / "nested" / "deeper" / "telemetry.jsonl"

    FileTelemetrySink(path, clock=lambda: NOW).stage("moderation", CTX, "proceed")

    assert path.exists()


# --- turn record ------------------------------------------------------------


def test_a_turn_is_written_when_it_opens_and_when_it_closes(tmp_path, a_turn):
    path = tmp_path / "turns.jsonl"
    sink = FileTurnSink(path, clock=lambda: NOW)

    sink.opened(CTX, a_turn(query="Wheat price?"))
    sink.closed(CTX, _finished())

    assert [r["event"] for r in _lines(path)] == ["opened", "closed"]


def test_the_opened_record_holds_the_question(tmp_path, a_turn):
    """This tier holds farmer content on purpose — it is the audit trail, and
    metadata alone cannot answer "what went wrong"."""

    path = tmp_path / "turns.jsonl"

    FileTurnSink(path, clock=lambda: NOW).opened(CTX, a_turn(query="Wheat price?"))

    assert _lines(path)[0]["turn"]["query"] == "Wheat price?"


def test_the_closed_record_holds_the_outcome_the_answer_and_the_sources(tmp_path):
    path = tmp_path / "turns.jsonl"

    FileTurnSink(path, clock=lambda: NOW).closed(CTX, _finished())

    record = _lines(path)[0]
    assert record["outcome"]["status"] == "answered"
    assert record["content"][0]["text"] == "Rs 2,275 per quintal."
    assert record["sources"][0]["name"] == "Agmarknet"


def test_geometry_is_never_written_to_the_sink(tmp_path, a_turn):
    """`api-contract.md` §6: region and area are written, geometry is not. A
    stable user id beside a precise point, over many turns, is a home address."""

    from dss.core.shared.models import Location

    path = tmp_path / "turns.jsonl"
    turn = a_turn(
        location=Location(region="IN-GJ", area="Anand", geometry=Geometry(coordinates=[72.93, 22.56]))
    )

    FileTurnSink(path, clock=lambda: NOW).opened(CTX, turn)

    written = path.read_text()
    assert "IN-GJ" in written
    assert "Anand" in written
    assert "72.93" not in written
    assert "geometry" not in written
