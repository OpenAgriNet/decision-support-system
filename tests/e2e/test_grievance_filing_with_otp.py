from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest
from docker.errors import DockerException
from fastapi.testclient import TestClient
from pytest_httpserver import HTTPServer
from testcontainers.community.redis import RedisContainer
from testcontainers.core.config import testcontainers_config
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

# Ryuk cannot mount the Podman socket on a Mac, and the fixture stops the container.
testcontainers_config.ryuk_disabled = True

MODEL = "azure:gpt-5.6-luna"
QUERY = "I want to file a PM-KISAN grievance"
FIXTURES = Path(__file__).parent / "fixtures"
SCHEMA_PACKS = FIXTURES / "network-specs" / "schema"

TICKET = "PMK-GRV-000777"
OTP = "4821"
VALUES = {
    "enrolmentId": "UP12345678A",
    "grievance.category.code": "G003",
    "grievance.description": "My last installment has not reached my account.",
}
INIT_FIELDS = {"@context", "@type", "informationMode", "scheme", "enrolmentId"}
DISCOVER_RESPONSE = json.loads(
    (FIXTURES / "discover_response_grievance.json").read_text()
)
PROVIDER_ID = next(
    catalog["provider"]["id"]
    for catalog in DISCOVER_RESPONSE["message"]["catalogs"]
    if any(
        resource["resourceAttributes"]["scheme"]["code"] == "PM-KISAN"
        for resource in catalog["resources"]
    )
)


class _Network:
    def __init__(self) -> None:
        self.init_requests: list[dict[str, Any]] = []
        self.support_requests: list[dict[str, Any]] = []

    def discover(self, request: Request) -> Response:
        if "Grievance" not in request.get_data(as_text=True):
            return self._json({"message": {"catalogs": []}})
        return self._json(DISCOVER_RESPONSE)

    def init(self, request: Request) -> Response:
        body = json.loads(request.get_data())
        self.init_requests.append(body)
        asked = body["message"]["contract"]["commitments"][0]["commitmentAttributes"]
        rejection = self._pack_rejection(asked)
        if rejection is not None:
            return rejection
        issued = {key: value for key, value in asked.items() if key != "enrolmentId"}
        issued["challenge"] = {
            "issued": {
                "method": "OTP",
                "channel": "SMS",
                "length": len(OTP),
                "valueType": "NUMERIC",
                "expiresAt": "2099-01-01T00:00:00Z",
            }
        }
        return self._json(
            {
                "message": {
                    "contract": {"commitments": [{"commitmentAttributes": issued}]}
                }
            }
        )

    def support(self, request: Request) -> Response:
        body = json.loads(request.get_data())
        self.support_requests.append(body)
        filed = body["message"]["support"]["channels"][0]
        rejection = self._pack_rejection(filed)
        if rejection is not None:
            return rejection
        # Echoes the OTP back, as a careless provider might: the DSS must
        # still keep it from the farmer.
        reply = {
            **filed,
            "case": {
                "ticketNo": TICKET,
                "status": {"code": "Registered"},
                "filedOn": "2026-11-10",
            },
        }
        return self._json({"message": {"support": {"channels": [reply]}}})

    def _pack_rejection(self, attributes: dict[str, Any]) -> Response | None:
        result = validate_resource_attributes(
            attributes, root=SCHEMA_PACKS, pack_name="Grievance"
        )
        if result.ok:
            return None
        return self._json(
            {"error": "SCH_SCHEMA_VALIDATION_FAILED", "details": list(result.errors)},
            status=400,
        )

    @staticmethod
    def _json(body: dict[str, Any], status: int = 200) -> Response:
        return Response(
            json.dumps(body), status=status, content_type="application/json"
        )


@pytest.fixture
def redis_url() -> Iterator[str]:
    container = RedisContainer("redis:7")
    try:
        container.start()
    except Exception as error:
        # start() can create the container and then fail, and Ryuk is off.
        with suppress(Exception):
            container.stop()
        if isinstance(error, DockerException):
            pytest.skip(f"no Docker for the Redis container: {error}")
        raise
    try:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"
    finally:
        container.stop()


@pytest.fixture
def live_network(httpserver: HTTPServer) -> _Network:
    network = _Network()
    httpserver.expect_request("/discover").respond_with_handler(network.discover)
    httpserver.expect_request("/init").respond_with_handler(network.init)
    httpserver.expect_request("/support").respond_with_handler(network.support)
    return network


@pytest.fixture
def live_app(
    httpserver: HTTPServer, live_network: _Network, redis_url: str, tmp_path: Path
):
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
        redis_url=redis_url,
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


def _form_in(message: dict[str, Any]) -> dict[str, Any]:
    return next(item for item in message["content"] if item["type"] == "form")


def _submission(
    form: dict[str, Any], values: dict[str, str], *, submission_id: str
) -> dict[str, Any]:
    return {
        "type": "form",
        "formId": form["formId"],
        "context": form["context"],
        "submissionId": submission_id,
        "fields": [{"name": name, "value": value} for name, value in values.items()],
    }


def test_a_pm_kisan_grievance_is_filed_after_an_otp(live_app, live_network, a_body):
    # One client context for every turn: each request outside one runs on a
    # fresh event loop, and the model client made on the first loop cannot
    # be used from the next.
    with TestClient(live_app) as client:
        asked = {"role": "user", "content": [{"type": "text", "text": QUERY}]}

        offered = _message(_post(client, a_body, [asked]))

        assert offered["outcome"]["status"] == "requires_input", offered
        details_form = _form_in(offered)
        assert details_form["context"] == {
            "scheme": "PM-KISAN",
            "category": "Grievance",
            "action": "support",
        }
        assert {field["name"] for field in details_form["fields"]} == set(VALUES)

        thread = [
            asked,
            {"role": "assistant", "content": offered["content"]},
            {
                "role": "user",
                "content": [_submission(details_form, VALUES, submission_id="s-1")],
            },
        ]

        waiting = _message(_post(client, a_body, thread))

        assert waiting["outcome"]["status"] == "requires_input", waiting
        init_form = _form_in(waiting)
        assert init_form["context"] == {**details_form["context"], "action": "init"}
        (otp_field,) = init_form["fields"]
        assert otp_field["kind"] == "secret"
        (init_body,) = live_network.init_requests
        sent_to_init = init_body["message"]["contract"]["commitments"][0][
            "commitmentAttributes"
        ]
        assert set(sent_to_init) <= INIT_FIELDS
        assert sent_to_init["enrolmentId"] == VALUES["enrolmentId"]
        assert live_network.support_requests == []

        thread += [
            {"role": "assistant", "content": waiting["content"]},
            {
                "role": "user",
                "content": [
                    _submission(
                        init_form, {otp_field["name"]: OTP}, submission_id="s-2"
                    )
                ],
            },
        ]

        filed = _message(_post(client, a_body, thread))

        assert filed["outcome"]["status"] == "answered", filed
        (text,) = filed["content"]
        assert text["type"] == "text"
        assert TICKET in text["text"], text
        assert OTP not in json.dumps(filed), filed

        (support_body,) = live_network.support_requests
        channel = support_body["message"]["support"]["channels"][0]
        assert channel["provider"]["id"] == PROVIDER_ID
        assert channel["challenge"]["response"]["value"] == OTP
        assert channel["enrolmentId"] == VALUES["enrolmentId"]
        assert channel["grievance"]["description"] == VALUES["grievance.description"]
