"""Tier 2 — the log bridge, read back through an in-memory exporter.

- Log lines carry span ids, so Grafana can show them next to their span.
- DEBUG lines hold farmers' words, so they must never be exported (ADR-0014).
- Each test passes its own `LoggerProvider`: the global one can be set once.
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
    """Match `app.py`; else INFO is dropped early and tests pass wrongly."""

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
    # A DEBUG logger, as `DSS_LOG_LEVEL=DEBUG` leaves it, so the bridge itself
    # must refuse the line. At INFO this would pass with the control removed.
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
    # Lets a trace link straight to its log lines, with no text parsing.
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
    # `create_app` can run twice in one process. Two handlers would double
    # every line, which looks like double traffic, not a bug.
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
    # Local runs have no endpoint. Nothing from an earlier boot may linger.
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
    # Exporting every library's logs would add cost nobody agreed to.
    logging.getLogger("httpx").info("HTTP Request: POST /select 200 OK")

    assert messages(exporter) == []
