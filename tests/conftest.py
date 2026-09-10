"""Shared pytest fixtures.

Builders, so a test names only the field it cares about and a contract change
touches one file.
"""

from __future__ import annotations

from typing import Any

import pytest

from dss.config.settings import Settings


@pytest.fixture(autouse=True)
def _settings_ignore_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's `.env` out of the suite.

    `Settings` reads `.env` from the working directory, so anyone with one —
    and `docs/RUNNING.md` tells you to make one — turns eight tests red for
    reasons unrelated to their change. Every one of them constructs
    `Settings()` expecting the network unset, which is only true on a machine
    with no local config.

    Autouse rather than opt-in: a test that forgot it would pass on CI and
    fail on a laptop, which is the worst way round.
    """

    monkeypatch.setitem(Settings.model_config, "env_file", None)


_TEXT = "What is the mandi price of wheat this week?"
_TRANSACTION = "9f2c1a8e-4b70-4d31-9c55-6f2e0b1d7a44"


class _Omit:
    """Sentinel: `a_body(message__userContext=omit)` drops the key entirely."""


_OMIT = _Omit()


def _body(**overrides: Any) -> dict[str, Any]:
    """A valid `/v1/turns` request body. camelCase, as the contract requires.

    Overrides are dotted with `__`: `a_body(message__input=[...])`.
    """

    body: dict[str, Any] = {
        "context": {
            "id": "api.dss.turn",
            "version": "1.0.0",
            "timestamp": "2026-09-04T08:00:00Z",
            "sessionId": "conv_8f3a1c",
            "transactionId": _TRANSACTION,
            "messageId": "msg_mandi_wheat_01",
        },
        "message": {
            "input": [{"role": "user", "content": [{"type": "text", "text": _TEXT}]}],
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
    for dotted, value in overrides.items():
        target = body
        *path, leaf = dotted.split("__")
        for key in path:
            target = target[key]
        if value is _OMIT:
            target.pop(leaf, None)
        else:
            target[leaf] = value
    return body


@pytest.fixture
def a_body():
    return _body


@pytest.fixture
def omit():
    return _OMIT


@pytest.fixture
def a_turn():
    """Build a `UserTurn`.

    `query=` is a convenience that sets `original_query` and `enriched_query`
    together — enrichment has not landed, so they mirror each other
    (`core/shared/models.py`).
    """

    from dss.core.shared.models import UserDetails, UserTurn

    def _turn(**overrides: Any) -> UserTurn:
        query = overrides.pop("query", _TEXT)
        fields: dict[str, Any] = {
            "original_query": query,
            "enriched_query": query,
            "session_id": "conv_8f3a1c",
            "transaction_id": _TRANSACTION,
            "source_lang": "hi",
            "target_lang": "hi",
            "channel": "web",
            "user": UserDetails(user_id="usr_9921"),
        }
        return UserTurn(**(fields | overrides))

    return _turn


@pytest.fixture
def a_context():
    from dss.core.shared.models import TurnContext

    return TurnContext(
        trace_id=_TRANSACTION, message_id="msg_mandi_wheat_01", session_id="conv_8f3a1c"
    )
