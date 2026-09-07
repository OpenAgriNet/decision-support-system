"""Shared builders. A test names only the field it cares about; everything else
comes from a valid default, so a contract change touches one file."""

from __future__ import annotations

from typing import Any

import pytest

_TEXT = "What is the mandi price of wheat this week?"


def _body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "context": {
            "id": "api.dss.turn",
            "version": "1.0.0",
            "timestamp": "2026-09-04T08:00:00Z",
            "sessionId": "conv_8f3a1c",
            "transactionId": "9f2c1a8e-4b70-4d31-9c55-6f2e0b1d7a44",
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


class _Omit:
    """Sentinel: `a_body(message__user_context=OMIT)` drops the key entirely."""


_OMIT = _Omit()


@pytest.fixture
def a_body():
    return _body


@pytest.fixture
def omit():
    return _OMIT


@pytest.fixture
def a_turn():
    from dss.core.shared.models import Channel, UserTurn

    def _turn(**overrides: Any) -> UserTurn:
        fields: dict[str, Any] = {
            "query": _TEXT,
            "source_lang": "hi",
            "target_lang": "hi",
            "channel": Channel.WEB,
            "user_id": "usr_9921",
        }
        return UserTurn(**(fields | overrides))

    return _turn


@pytest.fixture
def a_context():
    from dss.core.shared.models import TurnContext

    return TurnContext(trace_id="trc_1", session_id="conv_1", message_id="msg_in")
