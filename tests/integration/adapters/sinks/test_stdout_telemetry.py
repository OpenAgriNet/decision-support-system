"""The default telemetry sink. Tests the translation, not any business rule."""

from __future__ import annotations

import json

from dss.adapters.sinks.stdout import StdoutTelemetrySink
from dss.core.shared.models import TurnContext

CTX = TurnContext(trace_id="trc_1", session_id="conv_1", message_id="msg_in")


def test_each_stage_is_one_json_line(capsys):
    sink = StdoutTelemetrySink()

    sink.stage("moderation", CTX, "proceed")
    sink.stage("intent", CTX, "mandi-prices")

    lines = capsys.readouterr().out.strip().split("\n")
    assert [json.loads(line)["stage"] for line in lines] == ["moderation", "intent"]


def test_a_line_carries_the_trace_id_so_the_turn_can_be_joined(capsys):
    sink = StdoutTelemetrySink()

    sink.stage("moderation", CTX, "proceed")

    record = json.loads(capsys.readouterr().out)
    assert record["trace_id"] == "trc_1"
    assert record["outcome"] == "proceed"
