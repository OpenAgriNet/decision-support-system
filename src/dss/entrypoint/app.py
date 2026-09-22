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
from dss.adapters.observability.tracing import configure_tracing
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
    return app


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
    configure_tracing(
        intent_model=settings.intent_model,
        moderation_model=settings.moderation_model,
        planner_model=settings.planner_model,
        composer_model=settings.composer_model,
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
