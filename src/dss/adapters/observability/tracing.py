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
from collections.abc import Iterator
from contextlib import contextmanager

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

    endpoint = os.environ[_ENDPOINT]
    if not endpoint.startswith(("http://", "https://")):
        # Without a scheme the exporter fails once per batch, forever, with
        # `No connection adapters were found` — a line about the URL rather
        # than about the missing `http://`, buried in the export loop rather
        # than raised at startup. Say it once, at the top, in those terms.
        logger.error(
            "%s is %r, which has no scheme. Exports will fail with 'No "
            "connection adapters were found'. Prefix it with http:// or "
            "https:// — and note the OTLP/HTTP port is 4318, not 4317.",
            _ENDPOINT,
            endpoint,
        )

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
    # `scrubbing=False`: logfire's default scrubber redacts any attribute whose
    # key matches a sensitive-name pattern, and `langfuse.session.id` matches on
    # "session" — the id arrived at Langfuse as the literal string
    # "[Scrubbed due to 'session']", so filtering by session found nothing.
    #
    # Turning it off is consistent with the rest of this deployment rather than
    # a new exposure: `include_content` already puts the query and the answer in
    # the span, so a scrubber over the id attributes was protecting nothing that
    # was not already there.
    logfire.configure(send_to_logfire=False, console=False, scrubbing=False)
    Agent.instrument_all(settings)
    logger.info("tracing on, exporting to %s", endpoint)


@contextmanager
def turn_span(*, trace_id: str, message_id: str, session_id: str) -> Iterator[None]:
    """The span every one of a turn's other spans hangs off.

    Pydantic AI opens its own spans inside `Agent.run()` and the DSS does not
    own them, so the turn's ids cannot be set on them directly. They go on a
    parent span opened around the whole turn instead, which Langfuse reads as
    the trace root — and a root's attributes are what its trace is filed under.

    `langfuse.session.id` is the one Langfuse promotes to a first-class
    Session, so `sessionId` filters and groups in the UI. `transactionId` and
    `messageId` have no native field and go to trace metadata: the same string
    you grep for in the log, found through the metadata filter rather than the
    id box.

    Absent an OTLP endpoint there is no real tracer provider, so this is a
    no-op that costs a function call — which is every test and every local run
    without tracing configured.

    Lives here rather than in `orchestration/` so that package imports no
    telemetry SDK. When this grows past one function it should become a proper
    port under `ports/`, with this as its OpenTelemetry adapter.
    """

    from opentelemetry import trace

    tracer = trace.get_tracer("dss.orchestration")
    with tracer.start_as_current_span(
        "dss.turn",
        attributes={
            "langfuse.session.id": session_id,
            "langfuse.trace.metadata.transaction_id": trace_id,
            "langfuse.trace.metadata.message_id": message_id,
        },
    ):
        yield
