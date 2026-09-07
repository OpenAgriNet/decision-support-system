"""The wire is camelCase — `docs/api-contracts/openapi.yaml`.

Python stays snake_case (`CONVENTIONS.md`). The two are bridged by aliases on the
wire models alone, so `mapping.py` and everything inward never sees a camelCase
name. That is the whole reason the seam exists.

Input is strict: a snake_case key is an *unknown* field, and the contract sets
`additionalProperties: false`.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from tests.support.fakes import FakeRunner

from dss.adapters.http.v1 import schema
from dss.config.settings import Settings
from dss.core.shared.models import (
    Claim,
    TextBlock,
    TurnFinished,
    TurnOutcome,
    TurnStarted,
    TurnStatus,
)
from dss.entrypoint.app import build_app

CAMEL_REQUEST = {
    "context": {
        "id": "api.dss.turn",
        "version": "1.0.0",
        "timestamp": "2026-09-04T08:00:00Z",
        "sessionId": "conv_8f3a1c",
        "transactionId": "9f2c1a8e-4b70-4d31-9c55-6f2e0b1d7a44",
        "messageId": "msg_01",
    },
    "message": {
        "input": [
            {"role": "user", "content": [{"type": "text", "text": "Wheat price?"}]}
        ],
        "userContext": [{"type": "identity", "userId": "usr_9921"}],
        "attributes": {
            "sourceLanguage": "hi",
            "targetLanguage": "hi",
            "channel": "web",
            "location": {
                "region": "IN-GJ",
                "area": "Anand",
                "geometry": {"type": "Point", "coordinates": [72.93, 22.56]},
            },
            "response": {"maxCharacters": 1200},
        },
    },
}

ANSWER = TurnFinished(
    outcome=TurnOutcome(status=TurnStatus.ANSWERED, confidence=92),
    content=(TextBlock(text="Rs 2,275 per quintal.", source_ids=("src_1",)),),
)


def _client() -> TestClient:
    runner = FakeRunner([TurnStarted(), Claim(content=ANSWER.content[0]), ANSWER])
    return TestClient(build_app(runner=runner, settings=Settings()))


def test_a_camel_case_request_validates():
    body = schema.TurnRequest.model_validate(CAMEL_REQUEST)

    assert body.context.session_id == "conv_8f3a1c"
    assert body.message.attributes.source_language == "hi"
    assert body.message.attributes.response is not None
    assert body.message.attributes.response.max_characters == 1200
    assert body.message.user_context[0].user_id == "usr_9921"


@pytest.mark.parametrize(
    "path,snake,camel",
    [
        (("context",), "session_id", "sessionId"),
        (("message", "attributes"), "source_language", "sourceLanguage"),
    ],
)
def test_a_snake_case_key_is_an_unknown_field(path, snake, camel):
    """Strict on input: the contract forbids unknown properties, and a
    snake_case name is one."""

    body = json.loads(json.dumps(CAMEL_REQUEST))
    target = body
    for key in path:
        target = target[key]
    target[snake] = target.pop(camel)

    with pytest.raises(ValidationError):
        schema.TurnRequest.model_validate(body)


def test_the_json_response_is_camel_case():
    response = _client().post(
        "/v1/turns", json=CAMEL_REQUEST, headers={"Accept": "application/json"}
    )

    context = response.json()["context"]
    assert "traceId" in context
    assert "messageId" in context
    assert not [key for key in context if "_" in key]


def test_every_streamed_frame_is_camel_case():
    response = _client().post(
        "/v1/turns", json=CAMEL_REQUEST, headers={"Accept": "text/event-stream"}
    )

    for block in response.text.strip().split("\n\n"):
        payload = json.loads(block.split("\n")[1].removeprefix("data: "))
        assert "sequenceNumber" in payload["context"]
        assert not [key for key in payload["context"] if "_" in key]


def test_content_items_carry_camel_case_citations():
    response = _client().post(
        "/v1/turns", json=CAMEL_REQUEST, headers={"Accept": "application/json"}
    )

    assert response.json()["message"]["content"][0]["sourceIds"] == ["src_1"]
