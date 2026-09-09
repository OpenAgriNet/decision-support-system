"""The ASGI application.

`app.py` is the only module that both constructs and mounts: it asks
`composition` for a runner and hands it to the router. The router never builds
one, which is what keeps the HTTP layer testable against a fake.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import httpx2
from fastapi import FastAPI

from dss.adapters.http.v1 import schema
from dss.adapters.http.v1.router import turn_router
from dss.config.settings import Settings
from dss.entrypoint.composition import build_runner
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
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]] | None = None,
) -> FastAPI:
    """Wire a given runner. Tests pass a fake; `create_app` passes the real one.

    `lifespan` is optional because most tests hand in a fake runner that owns
    nothing to shut down. `create_app` passes one, because it builds the HTTP
    client and so has to close it.
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


def create_app(*, client: httpx2.AsyncClient | None = None) -> FastAPI:
    """The process entry point — `uvicorn --factory dss.entrypoint.app:create_app`.

    Builds the HTTP client the network adapters share and closes it on
    shutdown. It is built here, not in `build_runner`, because only the
    builder can close it — see `build_runner`'s docstring.

    Built unconditionally, even when the network is unwired: an `AsyncClient`
    opens no socket until a request is made, so an unused one costs a single
    `aclose()` and keeps the `network_enabled` decision in one place.

    `client` is for tests that need to assert it was closed; production passes
    nothing.
    """

    settings = Settings()
    # The timeout lives with construction now. Note it bounds discovery calls
    # too — one client serves both adapters.
    http_client = client or httpx2.AsyncClient(timeout=settings.select_timeout_seconds)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        try:
            yield
        finally:
            await http_client.aclose()

    return build_app(
        runner=build_runner(settings, client=http_client),
        settings=settings,
        lifespan=lifespan,
    )
