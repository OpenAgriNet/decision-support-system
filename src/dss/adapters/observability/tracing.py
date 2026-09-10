"""Agent runs as OpenTelemetry spans.

`Agent.instrument_all()` is the whole integration: one call and every Pydantic
AI agent's runs become spans — which agent ran, how long it took, how many
tokens, which tool it called, where it failed. The DSS builds agents in three
places and none of them changes.

What a span must *not* carry is message content. Pydantic AI includes it by
default — `gen_ai.input.messages` holds the farmer's query verbatim,
`pydantic_ai.all_messages` holds the whole conversation — and
`DSS_ARCHITECTURE.md` §6.1 forbids precisely that: "prompts containing
personal data" and "traces" are both named. So `include_content` is off unless
someone asks for it by name, which is a thing to do on a laptop and not in a
deployment.

Traces go wherever `OTEL_EXPORTER_OTLP_ENDPOINT` points — a local Collector,
Langfuse's OTLP endpoint, anything. Unset means tracing is off, which is the
quiet default for a local run and every test. Reading the standard variable
rather than a `DSS_`-prefixed one keeps the destination a deployment concern.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_ENDPOINT = "OTEL_EXPORTER_OTLP_ENDPOINT"
_INCLUDE_CONTENT = "DSS_TRACE_INCLUDE_MESSAGE_CONTENT"


def tracing_enabled() -> bool:
    """Whether an OTLP destination is configured."""

    return bool(os.environ.get(_ENDPOINT))


def instrumentation_settings(*, tracer_provider=None):
    """How much of each agent run to record.

    `include_content=False` strips every message body while keeping roles,
    part types, token counts and latency — so a span still says what ran and
    how it went, without saying what was asked.

    Only a literal `"true"` opts in. `1`, `yes` and a stray space all read as
    off: a permissive parser here would mean a typo in a deployment's
    environment sending a farmer's words out of the process.

    `tracer_provider` is for a test that needs to read the spans back. Left
    unset, Pydantic AI uses the global one.
    """

    from pydantic_ai.models.instrumented import InstrumentationSettings

    include = os.environ.get(_INCLUDE_CONTENT, "") == "true"
    return InstrumentationSettings(
        include_content=include, tracer_provider=tracer_provider
    )


def configure_tracing() -> None:
    """Send agent runs to the configured OTLP endpoint, if there is one.

    Called once from `create_app`. Absent an endpoint this does nothing at
    all — no exporter, no instrumentation, no warning, because a local run
    having none is normal rather than a misconfiguration.
    """

    if not tracing_enabled():
        return

    settings = instrumentation_settings()
    if settings.include_content:
        logger.warning(
            "%s is on: spans will carry the farmer's query and the composed "
            "answer verbatim. DSS_ARCHITECTURE.md §6.1 does not permit that "
            "in a deployment — use it locally and turn it off.",
            _INCLUDE_CONTENT,
        )

    import logfire
    from pydantic_ai.agent import Agent

    # `send_to_logfire=False`: logfire is used for its OTEL instrumentation of
    # Pydantic AI, not as a destination. Where traces go is the OTLP endpoint's
    # business, and shipping them to a third-party SaaS is not a decision this
    # module should make silently.
    logfire.configure(send_to_logfire=False, console=False)
    Agent.instrument_all(settings)
    logger.info("tracing on, exporting to %s", os.environ[_ENDPOINT])
