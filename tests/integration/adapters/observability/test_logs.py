"""Tier 2 — the log bridge, read back through an in-memory exporter.

Two claims are pinned here, and the second is the one that matters.

The bridge exists so Grafana can sit log lines next to the span they came
from. That is the first claim: an exported record carries the enclosing span's
trace and span ids, so the join needs no string parsing.

The second is a PII control. `log_external_request`/`log_external_response`
put `/discover` and `/select` bodies in the log at DEBUG, and those bodies are
the one place outside the `gen_ai.*` span attributes where a farmer's words
appear. ADR-0013 keeps them out of ClickHouse by giving the *handler* a level
of INFO — so `DSS_LOG_LEVEL=DEBUG` still prints them to stderr for whoever is
debugging, and still exports nothing. A handler that quietly starts accepting
DEBUG would leak them, and only a test that reads the exported records
notices.

Each test builds its own `LoggerProvider` and hands it in, rather than
installing one globally: `set_logger_provider` is one-shot per process, the
same as the tracer and meter providers.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import (
    InMemoryLogRecordExporter,
    SimpleLogRecordProcessor,
)
from opentelemetry.sdk.trace import TracerProvider

from dss.adapters.observability.logs import _DSS_LOGGER, configure_logs, reset_logs


@pytest.fixture(autouse=True)
def dss_logger_at_info() -> Iterator[None]:
    """What `_configure_logging` does in `app.py`, and nothing more.

    Without it the `dss` logger sits at NOTSET, inherits WARNING from root,
    and drops INFO before any handler sees it — so every assertion below would
    pass for the wrong reason.
    """

    dss_logger = logging.getLogger(_DSS_LOGGER)
    before = dss_logger.level
    dss_logger.setLevel(logging.INFO)
    yield
    dss_logger.setLevel(before)


@pytest.fixture
def exporter() -> Iterator[InMemoryLogRecordExporter]:
    exporter = InMemoryLogRecordExporter()
    provider = LoggerProvider()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    configure_logs(logger_provider=provider)
    yield exporter
    reset_logs()


def messages(exporter: InMemoryLogRecordExporter) -> list[str]:
    return [record.log_record.body for record in exporter.get_finished_logs()]


def test_info_is_exported(exporter: InMemoryLogRecordExporter) -> None:
    logging.getLogger("dss.trace").info("component=intent event=exit status=ok")

    assert messages(exporter) == ["component=intent event=exit status=ok"]


def test_debug_is_not_exported(exporter: InMemoryLogRecordExporter) -> None:
    # The logger is at DEBUG, as `DSS_LOG_LEVEL=DEBUG` leaves it. The record
    # reaches the handlers; the bridge is what must refuse it. Asserting
    # through a DEBUG-level logger is the whole point — a test that left the
    # logger at INFO would pass with the control removed.
    logger = logging.getLogger("dss.trace")
    logger.setLevel(logging.DEBUG)
    try:
        logger.debug('external=discovery event=request body={"query": "..."}')
        logger.info("component=discovery event=exit status=ok")
    finally:
        logger.setLevel(logging.NOTSET)

    assert messages(exporter) == ["component=discovery event=exit status=ok"]


def test_warning_and_error_are_exported(exporter: InMemoryLogRecordExporter) -> None:
    logger = logging.getLogger("dss.trace")
    logger.warning("component=planner event=exit status=degraded")
    logger.error("component=composer event=exit status=error")

    assert len(messages(exporter)) == 2


def test_record_carries_the_enclosing_span_ids(
    exporter: InMemoryLogRecordExporter,
) -> None:
    # What makes a log line clickable from a trace. `trace_log.py` already
    # writes both ids into the message text for grep; this is the same join
    # done by the backend instead.
    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("dss.turn") as span:
        expected = span.get_span_context()
        logging.getLogger("dss.trace").info("component=intent event=enter")

    record = exporter.get_finished_logs()[0].log_record
    assert record.trace_id == expected.trace_id
    assert record.span_id == expected.span_id


def test_reset_detaches_the_handler(exporter: InMemoryLogRecordExporter) -> None:
    reset_logs()

    logging.getLogger("dss.trace").info("component=intent event=exit status=ok")

    assert messages(exporter) == []


def test_configure_twice_attaches_one_handler() -> None:
    # `create_app` runs more than once in a process (tests, `--reload`). Two
    # handlers would export every line twice, which reads as double the traffic
    # on a dashboard rather than as a bug.
    provider = LoggerProvider()
    exporter = InMemoryLogRecordExporter()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    configure_logs(logger_provider=provider)
    configure_logs(logger_provider=provider)
    try:
        logging.getLogger("dss.trace").info("component=intent event=exit status=ok")
        assert len(exporter.get_finished_logs()) == 1
    finally:
        reset_logs()


def test_telemetry_off_leaves_no_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    # Every test and every local run without an endpoint lands here. Nothing
    # should be attached, and nothing from a previous boot should survive.
    from dss.adapters.observability.tracing import configure_telemetry

    provider = LoggerProvider()
    exporter = InMemoryLogRecordExporter()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    configure_logs(logger_provider=provider)

    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    configure_telemetry()

    logging.getLogger("dss.trace").info("component=intent event=exit status=ok")
    assert exporter.get_finished_logs() == ()


def test_only_the_dss_logger_is_bridged(exporter: InMemoryLogRecordExporter) -> None:
    # A handler on the root logger would export uvicorn's access log and every
    # library's chatter, which is a cost decision nobody made.
    logging.getLogger("httpx").info("HTTP Request: POST /select 200 OK")

    assert messages(exporter) == []
