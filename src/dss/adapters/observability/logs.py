"""Sends the app's log lines to the same endpoint as spans and metrics.

- Ids as fields, not text, so a dashboard can join a log line to its span.
- The handler's INFO level is the PII guard: DEBUG lines hold farmer words.
- logfire builds no logger provider, so this module builds one (same OTLP env).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opentelemetry.sdk._logs import LoggerProvider

logger = logging.getLogger(__name__)

# Not the root logger: that would also export uvicorn's and every library's lines.
_DSS_LOGGER = "dss"

# Lets a second `configure_logs` find our handler instead of adding another.
_MARKER = "_dss_otel_handler"

_provider: LoggerProvider | None = None
# A caller's provider is theirs to shut down, not ours.
_provider_is_ours = False


def configure_logs(*, logger_provider: LoggerProvider | None = None) -> None:
    """Bridge the `dss` logger to OTLP at INFO and above.

    - Only `configure_telemetry` calls this, so logs never run without tracing.
    - `logger_provider` lets a test read the records back.
    """

    global _provider, _provider_is_ours

    # The SDK's own `LoggingHandler` is deprecated and warns when built.
    from opentelemetry.instrumentation.logging.handler import LoggingHandler
    from opentelemetry.sdk._logs import LoggerProvider as SdkLoggerProvider
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

    _detach()

    ours = logger_provider is None
    if logger_provider is None:
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter

        logger_provider = SdkLoggerProvider()
        logger_provider.add_log_record_processor(
            BatchLogRecordProcessor(OTLPLogExporter())
        )

    # On the handler, not the logger: DEBUG still prints locally, never exports.
    handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
    setattr(handler, _MARKER, True)
    logging.getLogger(_DSS_LOGGER).addHandler(handler)

    _provider = logger_provider
    _provider_is_ours = ours


def reset_logs() -> None:
    """Take the bridge away, so a later boot without an endpoint starts clean."""

    global _provider, _provider_is_ours

    _detach()
    _provider = None
    _provider_is_ours = False


def _detach() -> None:
    """Remove our handler and flush it.

    Shut down, not dropped: the batch still holds lines that may explain a reload.
    """

    dss_logger = logging.getLogger(_DSS_LOGGER)
    for handler in [h for h in dss_logger.handlers if getattr(h, _MARKER, False)]:
        dss_logger.removeHandler(handler)

    if _provider is not None and _provider_is_ours:
        _provider.shutdown()
