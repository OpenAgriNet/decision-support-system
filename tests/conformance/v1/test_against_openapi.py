"""Validate real responses against the published contract, not against a reading of it.

Every other conformance test asserts what *I* understood the contract to say.
This one loads `docs/api-contracts/openapi.yaml` and validates rendered
responses against its schemas with `jsonschema`.

The difference is not academic. `exclude_none=True` silently drops any null, so
every field the contract marks `required` **and** nullable is a latent bug of the
same shape — and one of them (`outcome.cause`) went unnoticed through several
hand-written conformance tests. A machine reading the spec finds those; a human
re-reading their own assumptions does not.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from dss.config.settings import Settings
from dss.core.shared.models import (
    Claim,
    RefusalBlock,
    Source,
    SourceKind,
    TextBlock,
    TurnFinished,
    TurnOutcome,
    TurnStarted,
    TurnStatus,
)
from dss.entrypoint.app import build_app
from tests.support.fakes import FakeRunner

SPEC = Path(__file__).resolve().parents[3] / "docs" / "api-contracts" / "openapi.yaml"

ANSWER = TurnFinished(
    outcome=TurnOutcome(status=TurnStatus.ANSWERED, confidence=92),
    content=(TextBlock(text="Rs 2,275 per quintal.", source_ids=("src_1",)),),
    sources=(Source(id="src_1", name="Agmarknet", kind=SourceKind.PROVIDER),),
)
REFUSAL = TurnFinished(
    outcome=TurnOutcome(status=TurnStatus.REJECTED, confidence=98),
    content=(RefusalBlock(text="I can only answer agriculture questions."),),
)


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    return yaml.safe_load(SPEC.read_text())


def _validator(spec: dict[str, Any], name: str) -> Draft202012Validator:
    """Validate one component, with the whole document available for `$ref`."""

    schema = dict(spec["components"]["schemas"][name])
    schema["$defs"] = spec["components"]["schemas"]
    return Draft202012Validator(_rebase(schema))


def _rebase(node: Any) -> Any:
    """Point `#/components/schemas/X` at the local `$defs` copy."""

    if isinstance(node, dict):
        return {
            k: (
                v.replace("#/components/schemas/", "#/$defs/")
                if k == "$ref" and isinstance(v, str)
                else _rebase(v)
            )
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [_rebase(i) for i in node]
    return node


def _errors(spec: dict[str, Any], name: str, instance: Any) -> list[str]:
    return [
        f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
        for e in _validator(spec, name).iter_errors(instance)
    ]


def _client(finished: TurnFinished) -> TestClient:
    events = [TurnStarted()]
    events += [Claim(content=block) for block in finished.content]
    events.append(finished)
    return TestClient(build_app(runner=FakeRunner(events), settings=Settings()))


def test_the_spec_declares_the_schemas_this_suite_checks(spec):
    declared = set(spec["components"]["schemas"])

    assert {"TurnRequest", "TurnResponse", "Outcome", "ResponseContext"} <= declared


def test_the_request_builder_produces_a_valid_turn_request(spec, a_body):
    """The shared builder is what every other test sends. If it does not satisfy
    the published schema, the whole suite is testing a fiction."""

    assert not _errors(spec, "TurnRequest", a_body())


@pytest.mark.parametrize("finished", [ANSWER, REFUSAL], ids=["answered", "rejected"])
def test_the_json_response_validates_against_the_contract(spec, a_body, finished):
    response = _client(finished).post(
        "/v1/turns", json=a_body(), headers={"Accept": "application/json"}
    )

    assert not _errors(spec, "TurnResponse", response.json())


def test_every_streamed_frame_validates_against_the_contract(spec, a_body):
    response = _client(ANSWER).post(
        "/v1/turns", json=a_body(), headers={"Accept": "text/event-stream"}
    )

    problems = {}
    for block in response.text.strip().split("\n\n"):
        name = block.split("\n")[0].removeprefix("event: ")
        payload = json.loads(block.split("\n")[1].removeprefix("data: "))
        found = _errors(spec, "TurnEvent", payload)
        if found:
            problems[name] = found

    assert not problems
