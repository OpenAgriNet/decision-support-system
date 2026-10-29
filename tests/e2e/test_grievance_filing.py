"""Tier 7 — "I want to file a PMFBY grievance", then the filled-in form, end
to end over HTTP against the mock network.

Two turns through the shipped application. The first ends on an empty form;
the second sends it back and ends on the ticket the desk returned. What is
pinned: the `/support` body is the pack's own shape, the farmer's phone goes
to the desk and nowhere the farmer reads, and the reply names the ticket.

Same gate as the other e2e tests: no key in the environment, no run.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pytest_httpserver import HTTPServer
from werkzeug import Request, Response

from dss.config.settings import Settings
from dss.entrypoint.app import build_app
from dss.entrypoint.composition import build_runner
from tests.support.pack_schema import validate_resource_attributes

pytestmark = pytest.mark.skipif(
    not (os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")),
    reason="live model test — export AZURE_OPENAI_API_KEY and "
    "AZURE_OPENAI_ENDPOINT (MODEL below is an Azure deployment), or "
    "OPENAI_API_KEY for a deployment bound the other way",
)

MODEL = "azure:gpt-5.6-luna"
QUERY = "I want to file a PMFBY grievance"
FIXTURES = Path(__file__).parent / "fixtures"
SCHEMA_PACKS = FIXTURES / "network-specs" / "schema"

DISCOVER_RESPONSE = json.loads(
    (FIXTURES / "discover_response_grievance.json").read_text()
)
PROVIDER_ID = DISCOVER_RESPONSE["message"]["catalogs"][0]["provider"]["id"]
TICKET = "100626000099001"
PHONE = "9876543210"
VALUES = {
    "enrolmentId": "KA2026KH00123456",
    "applicantMobile": PHONE,
    "cropYear": "2026",
    "season": "Kharif",
    "grievance.description": "Cannot log in to the portal to see my enrolment.",
}


class _Network:
    """The OAN network adapter, faked: one PMFBY grievance desk. Records what
    the DSS sent; `/support` refuses a body the pack would refuse."""

    def __init__(self) -> None:
        self.discover_requests: list[dict[str, Any]] = []
        self.support_requests: list[dict[str, Any]] = []

    def discover(self, request: Request) -> Response:
        body = json.loads(request.get_data())
        self.discover_requests.append(body)
        if "Grievance" not in json.dumps(body):
            return self._json({"message": {"catalogs": []}})
        return self._json(DISCOVER_RESPONSE)

    def support(self, request: Request) -> Response:
        body = json.loads(request.get_data())
        self.support_requests.append(body)
        filed = body["message"]["support"]["channels"][0]
        result = validate_resource_attributes(
            filed, root=SCHEMA_PACKS, pack_name="Grievance"
        )
        if not result.ok:
            return self._json(
                {
                    "error": "SCH_SCHEMA_VALIDATION_FAILED",
                    "details": list(result.errors),
                },
                status=400,
            )
        reply = {
            **filed,
            "case": {
                "ticketNo": TICKET,
                "status": {"code": "Registered"},
                "filedOn": "2026-10-29",
            },
        }
        return self._json({"message": {"support": {"channels": [reply]}}})

    @staticmethod
    def _json(body: dict[str, Any], status: int = 200) -> Response:
        return Response(
            json.dumps(body), status=status, content_type="application/json"
        )


@pytest.fixture
def live_network(httpserver: HTTPServer) -> _Network:
    network = _Network()
    httpserver.expect_request("/discover").respond_with_handler(network.discover)
    httpserver.expect_request("/support").respond_with_handler(network.support)
    return network


@pytest.fixture
def live_app(httpserver: HTTPServer, live_network: _Network, tmp_path: Path):
    """The shipped application, assembled by the real `build_runner`."""

    base_url = httpserver.url_for("").rstrip("/")
    settings = Settings(
        stub_llm=False,
        intent_model=MODEL,
        moderation_model=MODEL,
        planner_model=MODEL,
        composer_model=MODEL,
        discovery_base_url=base_url,
        invocation_base_url=base_url,
        schema_pack_dir=SCHEMA_PACKS,
        schemes_config_path=FIXTURES / "schemes.csv",
        evidence_dir=tmp_path,
    )
    return build_app(runner=build_runner(settings), settings=settings)


def _post(client: TestClient, a_body, thread: list[dict[str, Any]]):
    return client.post(
        "/v1/turns",
        json=a_body(
            message__input=thread,
            message__attributes={
                "sourceLanguage": "en",
                "targetLanguage": "en",
                "channel": "web",
                "response": {"maxCharacters": 1200},
            },
        ),
        headers={"Accept": "application/json"},
    )


def _message(response) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    return response.json()["message"]


def test_a_grievance_is_offered_as_a_form_and_then_filed(
    live_app, live_network, a_body
):
    # One client context for both turns: each request outside one runs on a
    # fresh event loop, and the model client made on the first loop cannot
    # be used from the second.
    with TestClient(live_app) as client:
        asked = {"role": "user", "content": [{"type": "text", "text": QUERY}]}

        offered = _message(_post(client, a_body, [asked]))

        assert offered["outcome"]["status"] == "requires_input", offered
        form = next(item for item in offered["content"] if item["type"] == "form")
        assert form["context"] == {
            "scheme": "PMFBY",
            "category": "Grievance",
            "action": "support",
        }
        assert {field["name"] for field in form["fields"]} == set(VALUES)

        submission = {
            "type": "form",
            "formId": form["formId"],
            "context": form["context"],
            "submissionId": "submission-1",
            "fields": [
                {"name": name, "value": value} for name, value in VALUES.items()
            ],
        }
        thread = [
            asked,
            {"role": "assistant", "content": offered["content"]},
            {"role": "user", "content": [submission]},
        ]

        filed = _message(_post(client, a_body, thread))

        assert filed["outcome"]["status"] == "answered", filed
        (text,) = filed["content"]
        assert text["type"] == "text"
        assert TICKET in text["text"], text
        assert PHONE not in text["text"], text

        (support_body,) = live_network.support_requests
        channel = support_body["message"]["support"]["channels"][0]
        assert channel["@type"] == "openagrinet:Grievance"
        assert channel["provider"]["id"] == PROVIDER_ID
        assert channel["applicantMobile"] == PHONE
        assert channel["grievance"]["description"] == VALUES["grievance.description"]
