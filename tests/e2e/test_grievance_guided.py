"""Tier 7 — a farmer with a scheme problem is guided to the right form, end to
end over HTTP against the mock network.

Three turns through the shipped application: the problem with no scheme gets
one question and no form; the scheme named in reply gets the file form; "I
already filed" gets the status form. Structural only: outcomes, form items and
the form's context, never the composer's wording.

Same gate and fixtures as `test_grievance_filing.py`.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.e2e.test_grievance_filing import (  # noqa: F401  (fixtures)
    _message,
    _Network,
    _post,
    live_app,
    live_network,
    pytestmark,
)


def _user(text: str) -> dict[str, Any]:
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def _assistant(message: dict[str, Any]) -> dict[str, Any]:
    return {"role": "assistant", "content": message["content"]}


def _form_contexts(message: dict[str, Any]) -> list[dict[str, str]]:
    return [item["context"] for item in message["content"] if item["type"] == "form"]


def test_scheme_problem_guided_to_forms(
    live_app,  # noqa: F811
    live_network: _Network,  # noqa: F811
    a_body,
) -> None:
    # One client context for every turn: see `test_grievance_filing.py`.
    with TestClient(live_app) as client:
        problem = _user("My payment has not come")

        asked = _message(_post(client, a_body, [problem]))

        assert asked["outcome"]["status"] == "requires_input", asked
        assert _form_contexts(asked) == []
        assert [item["type"] for item in asked["content"]] == ["text"]

        thread = [problem, _assistant(asked), _user("PMFBY")]
        offered = _message(_post(client, a_body, thread))

        assert offered["outcome"]["status"] == "requires_input", offered
        assert _form_contexts(offered) == [
            {"scheme": "PMFBY", "category": "Grievance", "action": "support"}
        ]

        thread = [*thread, _assistant(offered), _user("I already filed it")]
        status = _message(_post(client, a_body, thread))

        assert status["outcome"]["status"] == "requires_input", status
        assert _form_contexts(status) == [
            {"scheme": "PMFBY", "category": "Grievance", "action": "status"}
        ]
        assert live_network.support_requests == []
