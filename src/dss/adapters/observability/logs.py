"""The app's log lines, sent where the spans and metrics already go.

`dss.trace` writes one line per stage and one per outbound call, each already
carrying `trace_id` and `span_id` as text so a person can grep a turn's log
back together. That works for a person with the file. It does not work for
someone looking at a dashboard: the lines are in a container's stdout and the
span is in ClickHouse, and nothing joins them.

This bridges the standard library's logger to OpenTelemetry, so a record
arrives as a log signal with the enclosing span's ids on it as *fields*. The
text format does not change — stderr still gets exactly what it got before.

**The handler's level is the PII control, and it is deliberate.** Bodies of
`/discover` and `/select` are logged at DEBUG (`observability/trace_log.py`),
clipped but verbatim, and they are the one place outside the `gen_ai.*` span
attributes where a farmer's words appear in telemetry. ADR-0013 keeps them out
of ClickHouse by filtering here rather than at the collector: the level sits on
the *handler*, not the logger, so `DSS_LOG_LEVEL=DEBUG` still prints bodies to
stderr for whoever is debugging, and still exports none of them. Leaking and
then filtering downstream would be one forgotten processor away from shipping
them.

**No LoggerProvider comes from logfire.** It configures a tracer and a meter
provider from the environment, not a logger provider, so unlike `metrics.py`
this module does build one — from the same standard `OTEL_EXPORTER_OTLP_*`
variables, so there is still one endpoint and one configuration path.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opentelemetry.sdk._logs import LoggerProvider

logger = logging.getLogger(__name__)

# The app's own logger, the one `_configure_logging` sets up in `app.py`.
# `dss.trace` and everything else under `dss.` propagates to it, and nothing
# else does — a handler on the root logger would export uvicorn's access log
# and every library's chatter, which is a cost nobody chose.
_DSS_LOGGER = "dss"

# Marks our handler so a second `configure_logs` finds it instead of adding
# another. The same trick `_configure_logging` uses for its stream handler.
_MARKER = "_dss_otel_handler"

_provider: LoggerProvider | None = None
# Whether we built `_provider`, and so whether shutting it down is ours to do.
# A provider handed in by a caller belongs to the caller: shutting that one
# down leaves them holding something dead, which is a rude way to end a test
# and a worse way to end a reload.
_provider_is_ours = False


def configure_logs(*, logger_provider: LoggerProvider | None = None) -> None:
    """Bridge the `dss` logger to OTLP at INFO and above.

    Called from `configure_telemetry`, so logs cannot be on with tracing off —
    they share the one endpoint, as metrics do.

    `logger_provider` is for a test that needs to read the records back. Left
    unset, one is built here around the OTLP exporter.
    """

    global _provider, _provider_is_ours

    # The handler comes from the instrumentation package, not the SDK: the
    # SDK's own `LoggingHandler` is deprecated in favour of this one and warns
    # on construction.
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

    # INFO on the handler. See the module docstring — this line is the control.
    handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
    setattr(handler, _MARKER, True)
    logging.getLogger(_DSS_LOGGER).addHandler(handler)

    _provider = logger_provider
    _provider_is_ours = ours


def reset_logs() -> None:
    """Take the bridge away again.

    `create_app` runs more than once in a process (tests, `--reload`), and a
    boot with no OTLP endpoint must leave nothing behind from the last one.
    """

    global _provider, _provider_is_ours

    _detach()
    _provider = None
    _provider_is_ours = False


def _detach() -> None:
    """Remove our handler, and flush whatever it had not sent yet.

    Shutting our own provider down rather than dropping it: a batch processor
    holds records for up to its schedule, and on a `--reload` those are the
    lines explaining whatever prompted the reload.
    """

    dss_logger = logging.getLogger(_DSS_LOGGER)
    for handler in [h for h in dss_logger.handlers if getattr(h, _MARKER, False)]:
        dss_logger.removeHandler(handler)

    if _provider is not None and _provider_is_ours:
        _provider.shutdown()
