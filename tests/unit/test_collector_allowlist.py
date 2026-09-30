"""The collector keeps only named attributes on the way to ClickHouse.

Nothing redacts a farmer's words before the collector yet, so this processor is
the only thing between them and a shared database. A denylist of content keys
missed `final_result` and exception text; an allowlist cannot miss a key it
never heard of. These tests read the allowlist the collector runs and check it
against keys we know carry content and keys we know we need.

Python's `re` stands in for the collector's RE2 — the pattern uses nothing the
two disagree on.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

OTEL = Path(__file__).parents[2] / "otel"
DEPLOYMENT = OTEL / "collector.yaml"
LAPTOP = OTEL / "collector.local.yaml"
PROCESSOR = "transform/clickhouse_allowlist"

# Where message text lives today, plus names a pydantic-ai upgrade could invent.
CONTENT = (
    "gen_ai.input.messages",
    "gen_ai.output.messages",
    "gen_ai.system_instructions",
    "pydantic_ai.all_messages",
    "gen_ai.tool.call.arguments",
    "gen_ai.tool.call.result",
    "final_result",
    "logfire.msg",
    "model_request_parameters",
    "gen_ai.tool.definitions",
    "gen_ai.input.messages.v2",
    "gen_ai.prompt",
    "gen_ai.completion",
    "pydantic_ai.new_content",
)

# What the dashboard, a debugging session or a later story needs.
NEEDED = (
    "status",
    "first_delta_ms",
    "composed_ms",
    "asks_total",
    "asks_failed",
    "dss.ask.categories",
    "dss.ask.interactions",
    "dss.ask.subjects",
    "intent_model",
    "composer_model",
    "session.id",
    "langfuse.session.id",
    "langfuse.trace.metadata.transaction_id",
    "gen_ai.request.model",
    "gen_ai.response.model",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "gen_ai.aggregated_usage.input_tokens",
    "gen_ai.agent.name",
    "gen_ai.tool.name",
    "operation.cost",
    "provider_id",
    "capability",
    "answered",
    "failure_class",
    "offered_provider_ids",
    "answered_provider_ids",
)


def _config(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def _statements(path: Path, context: str) -> list[str]:
    """The statements acting on one context, read from their path prefix
    (`span.`, `spanevent.`) — the collector infers the context the same way."""

    processor = _config(path)["processors"][PROCESSOR]
    return [
        statement
        for group in processor["trace_statements"]
        for statement in group["statements"]
        if f"({context}." in statement
    ]


def _span_pattern(path: Path) -> re.Pattern[str]:
    (statement,) = [
        s
        for s in _statements(path, "span")
        if s.startswith("keep_matching_keys(span.attributes")
    ]
    pattern = re.search(r'keep_matching_keys\(span\.attributes, "(.*)"\)$', statement)
    assert pattern, statement
    return re.compile(pattern.group(1).replace("\\\\", "\\"))


def test_the_laptop_runs_the_same_allowlist_as_a_deployment() -> None:
    """Otherwise the control is tested locally and a different one ships."""

    assert (
        _config(LAPTOP)["processors"][PROCESSOR]
        == _config(DEPLOYMENT)["processors"][PROCESSOR]
    )


@pytest.mark.parametrize(
    ("path", "pipeline"),
    [(DEPLOYMENT, "traces/clickhouse"), (LAPTOP, "traces/stripped")],
)
def test_the_clickhouse_branch_applies_it_first(path, pipeline) -> None:
    processors = _config(path)["service"]["pipelines"][pipeline]["processors"]

    assert processors[0] == PROCESSOR


def test_it_fails_closed() -> None:
    """A statement that errors must drop the batch, not pass it through."""

    assert _config(DEPLOYMENT)["processors"][PROCESSOR]["error_mode"] == "propagate"


@pytest.mark.parametrize("key", CONTENT)
def test_no_content_key_is_kept(key) -> None:
    assert not _span_pattern(DEPLOYMENT).fullmatch(key)


@pytest.mark.parametrize("key", NEEDED)
def test_every_needed_key_is_kept(key) -> None:
    assert _span_pattern(DEPLOYMENT).fullmatch(key)


def test_exception_text_is_dropped_but_its_type_kept() -> None:
    """Spans say what failed by type, never by message."""

    events = " ".join(_statements(DEPLOYMENT, "spanevent"))
    span = " ".join(_statements(DEPLOYMENT, "span"))

    assert 'keep_keys(spanevent.attributes, ["exception.type"' in events
    assert "exception.message" not in events
    assert 'set(span.status.message, "")' in span
