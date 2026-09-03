"""Tier 3 — the Experience-API envelope mapping (ADR-0004).

``to_user_turn`` is the boundary that turns provider-shaped JSON into the domain
``UserTurn``. These lock the contract: current query, history order, attribute
mapping, and expired-token-as-absent.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from dss.orchestration.envelope import TurnEnvelope, to_user_turn

_NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)

# The sample thread from the feedback: two prior turns, then a follow-up.
_SAMPLE = {
    "input": [
        {
            "role": "user",
            "content": [{"type": "text", "text": "What is wrong with my crop?"}],
        },
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Wheat is ₹2,275 per quintal at Anand mandi."}
            ],
        },
        {"role": "user", "content": [{"type": "text", "text": "And potato?"}]},
    ],
    "user_context": {"user_id": "u-123"},
    "attributes": {
        "sourceLanguage": "en",
        "targetLanguage": "hi",
        "channel": "whatsapp",
        "location": {
            "region": "IN-GJ",
            "area": "Anand",
            "geometry": {"type": "Point", "coordinates": [72.93, 22.56]},
        },
        "response": {"max_characters": 1200},
    },
}


def _map(payload: dict):
    return to_user_turn(TurnEnvelope.model_validate(payload), now=_NOW)


def test_last_user_message_is_the_current_query() -> None:
    turn = _map(_SAMPLE)
    assert turn.original_query == "And potato?"
    assert turn.enriched_query == "And potato?"  # mirrors until enrichment lands


def test_prior_messages_become_ordered_history() -> None:
    turn = _map(_SAMPLE)
    assert [(m.role, m.text) for m in turn.history] == [
        ("user", "What is wrong with my crop?"),
        ("assistant", "Wheat is ₹2,275 per quintal at Anand mandi."),
    ]


def test_attributes_map_onto_the_turn() -> None:
    turn = _map(_SAMPLE)
    assert turn.source_lang == "en"
    assert turn.target_lang == "hi"
    assert turn.channel == "whatsapp"
    assert turn.response_max_chars == 1200
    assert turn.location is not None
    assert turn.location.area == "Anand"
    assert turn.location.geometry.coordinates == [72.93, 22.56]


def test_user_id_is_carried() -> None:
    turn = _map(_SAMPLE)
    assert turn.user.user_id == "u-123"
    assert turn.session_id == "u-123"  # envelope has no session id; key on user id


def test_content_may_be_a_bare_string() -> None:
    payload = {
        "input": [{"role": "user", "content": "And potato?"}],
        "attributes": {
            "sourceLanguage": "en",
            "targetLanguage": "en",
            "channel": "web",
        },
    }
    assert _map(payload).original_query == "And potato?"


def test_live_reference_token_is_carried() -> None:
    payload = {
        **_SAMPLE,
        "user_context": {
            "user_id": "u-1",
            "reference_token": "tok-abc",
            "issuer": "gov",
            "expires_at": "2099-01-01T00:00:00Z",
        },
    }
    ref = _map(payload).user.reference
    assert ref is not None
    assert ref.value == "tok-abc"
    assert ref.issuer == "gov"


def test_expired_reference_token_counts_as_absent() -> None:
    payload = {
        **_SAMPLE,
        "user_context": {
            "user_id": "u-1",
            "reference_token": "tok-old",
            "expires_at": "2020-01-01T00:00:00Z",
        },
    }
    assert _map(payload).user.reference is None


def test_envelope_with_no_user_message_is_rejected() -> None:
    payload = {
        "input": [{"role": "assistant", "content": "hello"}],
        "attributes": {
            "sourceLanguage": "en",
            "targetLanguage": "en",
            "channel": "web",
        },
    }
    with pytest.raises(ValueError):
        _map(payload)
