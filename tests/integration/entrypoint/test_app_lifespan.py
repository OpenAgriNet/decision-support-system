"""Tier 3 — the app closes the HTTP client it opened.

`httpx2.AsyncClient` owns a connection pool. Whoever builds it is the only one
that can close it, and `build_runner` deliberately does not: the two network
adapters hold it privately, so a client built in there could never be reached
again. `create_app` builds it and a lifespan closes it.
"""

from __future__ import annotations

from pathlib import Path

import httpx2
from fastapi.testclient import TestClient

from dss.config.settings import Settings
from dss.entrypoint.app import create_app


def _env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DSS_STUB_LLM", "true")
    monkeypatch.setenv("DSS_EVIDENCE_DIR", str(tmp_path))


def test_the_lifespan_closes_the_client_on_shutdown(monkeypatch, tmp_path) -> None:
    _env(monkeypatch, tmp_path)
    client = httpx2.AsyncClient()
    app = create_app(client=client)

    assert not client.is_closed  # nothing has run yet

    with TestClient(app):  # __enter__ starts the lifespan, __exit__ shuts it down
        assert not client.is_closed  # still serving

    assert client.is_closed


def test_a_client_is_built_even_with_the_network_disabled(
    monkeypatch, tmp_path
) -> None:
    """The unwired branch never calls out, but it still gets a client.

    An `AsyncClient` is lazy — constructing one opens no socket — so building
    it unconditionally costs nothing and keeps the `network_enabled` decision
    in one place instead of two.
    """

    _env(monkeypatch, tmp_path)
    assert not Settings().network_enabled

    client = httpx2.AsyncClient()
    with TestClient(create_app(client=client)):
        pass

    assert client.is_closed
