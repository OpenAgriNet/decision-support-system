"""Tier 7 — "What happened to my PMFBY complaint?", then the filled-in status
form, end to end over HTTP against a faked network.

Two turns through the shipped application. The first ends on an empty status
form; the second sends it back and ends on the case the desk returned. What is
pinned: the `/status` body is the pack's own shape on a commitment, the phone
and the ticket go to the desk and nowhere the farmer reads, and the reply says
where the case stands. A desk that knows no such case keeps the form.

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

from dss.config.clarification_text_loader import load_clarification_text
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
QUERY = "What happened to my PMFBY complaint?"
FIXTURES = Path(__file__).parent / "fixtures"
SCHEMA_PACKS = FIXTURES / "network-specs" / "schema"

DISCOVER_RESPONSE = json.loads(
    (FIXTURES / "discover_response_grievance.json").read_text()
)
TICKET = "100626000099001"
PHONE = "9876543210"
VALUES = {"applicantMobile": PHONE, "case.ticketNo": TICKET}


class _Network:
    """The OAN network adapter, faked: one PMFBY grievance desk. Records what
    the DSS sent; `/status` refuses a body the pack would refuse, and a case
    it does not hold."""

    def __init__(self, *, holds_the_case: bool = True) -> None:
        self.holds_the_case = holds_the_case
        self.status_requests: list[dict[str, Any]] = []

    def discover(self, request: Request) -> Response:
        body = json.loads(request.get_data())
        if "Grievance" not in json.dumps(body):
            return self._json({"message": {"catalogs": []}})
        return self._json(DISCOVER_RESPONSE)

    def status(self, request: Request) -> Response:
        body = json.loads(request.get_data())
        self.status_requests.append(body)
        commitment = body["message"]["contract"]["commitments"][0]
        asked = commitment["commitmentAttributes"]
        result = validate_resource_attributes(
            asked, root=SCHEMA_PACKS, pack_name="Grievance"
        )
        if not result.ok:
            return self._json(
                {
                    "error": "SCH_SCHEMA_VALIDATION_FAILED",
                    "details": list(result.errors),
                },
                status=400,
            )
        if not self.holds_the_case:
            return self._json({"error": "no case for that ticket"}, status=400)
        read = {
            **asked,
            "case": {
                "ticketNo": TICKET,
                "status": {"code": "UnderReview"},
                "filedOn": "2026-09-28",
                "cropName": "Paddy",
            },
        }
        return self._json(
            {
                "message": {
                    "contract": {
                        "commitments": [
                            {
                                "resources": commitment["resources"],
                                "commitmentAttributes": read,
                            }
                        ]
                    }
                }
            }
        )

    @staticmethod
    def _json(body: dict[str, Any], status: int = 200) -> Response:
        return Response(
            json.dumps(body), status=status, content_type="application/json"
        )


def _serve(httpserver: HTTPServer, network: _Network) -> _Network:
    httpserver.expect_request("/discover").respond_with_handler(network.discover)
    httpserver.expect_request("/status").respond_with_handler(network.status)
    return network


def _app(httpserver: HTTPServer, tmp_path: Path):
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


def _offer_then_submit(client: TestClient, a_body) -> dict[str, Any]:
    """Both turns. One client context for both: a model client made on the
    first turn's event loop cannot be used from a second one."""

    asked = {"role": "user", "content": [{"type": "text", "text": QUERY}]}

    offered = _message(_post(client, a_body, [asked]))

    assert offered["outcome"]["status"] == "requires_input", offered
    form = next(item for item in offered["content"] if item["type"] == "form")
    assert form["context"] == {
        "scheme": "PMFBY",
        "category": "Grievance",
        "action": "status",
    }
    assert {field["name"] for field in form["fields"]} == set(VALUES)

    submission = {
        "type": "form",
        "formId": form["formId"],
        "context": form["context"],
        "submissionId": "submission-1",
        "fields": [{"name": name, "value": value} for name, value in VALUES.items()],
    }
    thread = [
        asked,
        {"role": "assistant", "content": offered["content"]},
        {"role": "user", "content": [submission]},
    ]
    return _message(_post(client, a_body, thread))


def test_a_status_is_offered_as_a_form_and_then_read(
    httpserver: HTTPServer, tmp_path: Path, a_body
):
    network = _serve(httpserver, _Network())

    with TestClient(_app(httpserver, tmp_path)) as client:
        read = _offer_then_submit(client, a_body)

    assert read["outcome"]["status"] == "answered", read
    (text,) = read["content"]
    assert text["type"] == "text"
    assert "under review" in text["text"].casefold(), text
    assert "paddy" in text["text"].casefold(), text
    assert PHONE not in text["text"], text
    assert TICKET not in text["text"], text

    (status_body,) = network.status_requests
    assert status_body["context"]["action"] == "status"
    (commitment,) = status_body["message"]["contract"]["commitments"]
    asked = commitment["commitmentAttributes"]
    assert asked["@type"] == "openagrinet:Grievance"
    assert asked["applicantMobile"] == PHONE
    assert asked["case"] == {"ticketNo": TICKET}


def test_a_case_the_desk_does_not_hold_keeps_the_form(
    httpserver: HTTPServer, tmp_path: Path, a_body
):
    _serve(httpserver, _Network(holds_the_case=False))

    with TestClient(_app(httpserver, tmp_path)) as client:
        unread = _offer_then_submit(client, a_body)

    assert unread["outcome"]["status"] == "unavailable", unread
    assert unread["outcome"]["cause"] == "provider_rejected"
    text, kept = unread["content"]
    assert text["text"] == load_clarification_text().status_not_found
    assert kept["type"] == "form_error"
    assert kept["fields"] == []
