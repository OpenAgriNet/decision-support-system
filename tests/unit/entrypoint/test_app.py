"""The process entry point, exercised the way uvicorn calls it."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dss.entrypoint.app import create_app


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

    # `no_match`, not `answered`: the real orchestrator runs intent, moderation
    # and discovery, but the OAN network is not wired in a local build, so
    # discovery finds nobody and the planner/composer are never reached. That is
    # the honest outcome without providers — and it is not the failed-closed
    # `moderation_unavailable`, so it still proves moderation PROCEEDED and the
    # whole real pipeline ran and wrote its evidence.
    assert response.json()["message"]["outcome"]["status"] == "no_match"
    assert (tmp_path / "telemetry.jsonl").exists()
    assert (tmp_path / "turns.jsonl").exists()
