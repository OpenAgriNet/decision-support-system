"""The process entry point, exercised the way uvicorn calls it."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dss.entrypoint.app import create_app
from tests.support.fakes import FakeRunner


def test_create_app_closes_the_network_client_on_shutdown(monkeypatch) -> None:
    """The lifespan releases what `build_runner_with_lifecycle` handed it. A
    real deployment's `httpx.AsyncClient` would otherwise leak its connection
    pool past shutdown — here the close is a spy, so the wiring is what's under
    test, not httpx."""

    closed: list[bool] = []

    async def _aclose() -> None:
        closed.append(True)

    monkeypatch.setattr(
        "dss.entrypoint.app.build_runner_with_lifecycle",
        lambda settings: (FakeRunner([]), _aclose),
    )

    # Entering and leaving the TestClient context runs startup then shutdown.
    with TestClient(create_app()):
        assert closed == [], "not closed while the server is up"

    assert closed == [True], "closed exactly once, on shutdown"


def test_create_app_builds_an_application_serving_the_turn_route(tmp_path, monkeypatch):
    """Asserted through a request rather than by reading `app.routes`, which
    holds an opaque router object once a router is included."""

    monkeypatch.setenv("DSS_EVIDENCE_DIR", str(tmp_path))

    app = create_app()

    assert isinstance(app, FastAPI)
    response = TestClient(app).post("/v1/turns", json={})
    assert response.status_code == 422, "the route is mounted and validating"


def test_the_real_composition_writes_evidence_where_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("DSS_EVIDENCE_DIR", str(tmp_path))
    # Without this the composition root builds a real Pydantic AI provider and
    # the turn would reach the network.
    monkeypatch.setenv("DSS_STUB_LLM", "true")
    body = {
        "context": {
            "id": "api.dss.turn",
            "version": "1.0.0",
            "timestamp": "2026-09-07T08:00:00Z",
            "sessionId": "conv_1",
            "transactionId": "9f2c1a8e-4b70-4d31-9c55-6f2e0b1d7a44",
        },
        "message": {
            "input": [
                {"role": "user", "content": [{"type": "text", "text": "Wheat price?"}]}
            ],
            "attributes": {
                "sourceLanguage": "en",
                "targetLanguage": "en",
                "channel": "web",
            },
        },
    }

    response = TestClient(create_app()).post(
        "/v1/turns", json=body, headers={"Accept": "application/json"}
    )

    # `requires_input`, not `answered`: the real orchestrator runs intent and
    # moderation, but this body carries no location and "Wheat price?" names no
    # place, so there is nowhere to search and the turn asks for a district
    # before discovery. It is not the failed-closed `moderation_unavailable`, so
    # it still proves moderation PROCEEDED and the real pipeline ran and wrote
    # its evidence.
    assert response.json()["message"]["outcome"]["status"] == "requires_input"
    assert (tmp_path / "telemetry.jsonl").exists()
    assert (tmp_path / "turns.jsonl").exists()
