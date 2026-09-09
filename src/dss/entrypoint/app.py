"""The ASGI application.

`app.py` is the only module that both constructs and mounts: it asks
`composition` for a runner and hands it to the router. The router never builds
one, which is what keeps the HTTP layer testable against a fake.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.types import Lifespan

from dss.adapters.http.v1 import schema
from dss.adapters.http.v1.router import turn_router
from dss.config.settings import Settings
from dss.entrypoint.composition import build_runner_with_lifecycle
from dss.ports.turn import TurnRunner

TITLE = "Decision Support System"
DESCRIPTION = (
    "One turn in, a stream of claims out, then one final event. Internal to the "
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


def create_app() -> FastAPI:
    """The process entry point — `uvicorn --factory dss.entrypoint.app:create_app`."""

    settings = Settings()
    runner, aclose = build_runner_with_lifecycle(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # Startup is nothing to do; the client was opened at wiring time. On
        # shutdown, release it — a leaked `httpx2.AsyncClient` holds its
        # connection pool open past the process's intent to stop.
        try:
            yield
        finally:
            await aclose()

    return build_app(runner=runner, settings=settings, lifespan=lifespan)
