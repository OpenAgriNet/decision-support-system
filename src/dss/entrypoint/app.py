"""The ASGI application.

`app.py` is the only module that both constructs and mounts: it asks
`composition` for a runner and hands it to the router. The router never builds
one, which is what keeps the HTTP layer testable against a fake.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.types import Lifespan

from dss.adapters.http.v1 import schema
from dss.adapters.http.v1.router import turn_router
from dss.adapters.observability.tracing import configure_telemetry, tracing_enabled
from dss.config.settings import Settings
from dss.entrypoint.composition import build_runner_with_lifecycle
from dss.ports.turn import TurnRunner

TITLE = "Decision Support System"
DESCRIPTION = (
    "One turn in, the answer streamed as it is written, then one final event. "
    "`Accept` selects a single JSON body or the event stream. Internal to the "
    "deployment: no authentication, no CORS."
)


def build_app(
    *,
    runner: TurnRunner,
    settings: Settings,
    lifespan: Lifespan[FastAPI] | None = None,
) -> FastAPI:
    """Wire a given runner. Tests pass a fake; `create_app` passes the real one.

    `lifespan` is optional so tests can mount a fake runner with no resources to
    release; `create_app` passes one that closes the network client on shutdown.
    """

    app = FastAPI(
        title=TITLE,
        description=DESCRIPTION,
        version=settings.dss_release,
        lifespan=lifespan,
        # No auth and no CORS by design — only this deployment's own channel
        # services reach this port.
    )
    app.include_router(turn_router(runner=runner, settings=settings))
    _publish_wire_schemas(app)
    _instrument_http(app)
    return app


def _instrument_http(app: FastAPI, *, meter_provider=None) -> None:  # noqa: ANN001
    """Request duration, count, route, method and status, for every endpoint.

    Auto-instrumentation rather than a middleware of our own: the same numbers,
    under the names a dashboard already knows, with nothing to keep in step
    with FastAPI.

    Note what this measures on `POST /v1/turns`. The turn streams, so the
    request is not finished until the last claim is written — this is time to
    the *last* word, where `dss.turn.first_delta.duration` is the wait the
    farmer feels. The two differ by a lot and neither replaces the other;
    written down here so one is not later deleted as a duplicate of the other.

    Metrics only, no spans. The instrumentor's spans sat above `dss.turn` as
    the trace root, added an ASGI send/receive span per streamed frame, traced
    the healthcheck, and carried the raw query string and full exception
    messages — where our spans say what failed by type, never by message
    (§6.1). `dss.turn` already covers the turn; a no-op tracer drops the rest.

    Gated on telemetry being configured, like everything else: with no
    endpoint, a request should not pay for metrics nobody exports.
    """

    if not tracing_enabled():
        return

    # Ask for the stable HTTP names before instrumenting. Without this the
    # instrumentor still emits the superseded ones — `http.server.duration` in
    # milliseconds instead of `http.server.request.duration` in seconds — and a
    # dashboard built on the documented name would find nothing. Set here
    # rather than in the environment so a local run and a deployment publish
    # the same names.
    os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "http")

    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.trace import NoOpTracerProvider

    FastAPIInstrumentor.instrument_app(
        app, tracer_provider=NoOpTracerProvider(), meter_provider=meter_provider
    )


def _publish_wire_schemas(app: FastAPI) -> None:
    """Put the wire models into `components.schemas`.

    The route declares its body by reference (the endpoint parses the body
    itself, so FastAPI cannot infer it). Those references only resolve if the
    definitions are published here, so the two belong together.
    """

    generate = app.openapi

    def openapi() -> dict:
        document = generate()
        components = document.setdefault("components", {}).setdefault("schemas", {})
        components.update(schema.component_schemas())
        return document

    app.openapi = openapi  # type: ignore[method-assign]


def _configure_logging() -> None:
    """Make the app's own loggers (`dss.*`, incl. `dss.trace`) show up.

    uvicorn configures its loggers but leaves ours at the root default, so
    INFO lines from the pipeline would be swallowed. Give the `dss` logger its
    own handler at `DSS_LOG_LEVEL` (default INFO) and stop propagation so lines
    are not also emitted by the root handler. Idempotent — safe if `create_app`
    is called more than once (tests, reload).
    """

    level = os.environ.get("DSS_LOG_LEVEL", "INFO").upper()
    dss_logger = logging.getLogger("dss")
    dss_logger.setLevel(level)
    if not any(getattr(h, "_dss_handler", False) for h in dss_logger.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        handler._dss_handler = True  # type: ignore[attr-defined]
        dss_logger.addHandler(handler)
    dss_logger.propagate = False


def create_app() -> FastAPI:
    """The process entry point — `uvicorn --factory dss.entrypoint.app:create_app`."""

    _configure_logging()
    settings = Settings()
    configure_telemetry(
        intent_model=settings.intent_model,
        moderation_model=settings.moderation_model,
        planner_model=settings.planner_model,
        composer_model=settings.composer_model,
        model_profile=settings.model_profile,
    )
    runner, aclose = build_runner_with_lifecycle(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # Startup is nothing to do; the client was opened at wiring time. On
        # shutdown, release it — a leaked `httpx.AsyncClient` holds its
        # connection pool open past the process's intent to stop.
        try:
            yield
        finally:
            await aclose()

    return build_app(runner=runner, settings=settings, lifespan=lifespan)
