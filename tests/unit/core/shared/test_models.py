"""Tier-1 unit tests for the shared ``UserTurn`` envelope.

The contract rules that matter downstream are enforced structurally: required
fields, the closed channel set, unknown-field rejection (§2), and an opaque
default ``subject_ref`` carrying no PII.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dss.core.shared import SubjectRef, UserTurn


def _minimal(**overrides):
    base = {
        "query": "what is the price of potato?",
        "source_lang": "en",
        "target_lang": "en",
        "channel": "web",
        "session_id": "sess_1",
    }
    base.update(overrides)
    return UserTurn(**base)


def test_minimal_turn_has_sensible_defaults():
    turn = _minimal()
    assert turn.history == ()
    assert turn.location is None
    assert turn.response_max_chars is None
    assert turn.trace_id is None
    # An anonymous, empty opaque reference — no PII.
    assert turn.subject_ref == SubjectRef(user_id="anonymous")


def test_query_may_not_be_empty():
    with pytest.raises(ValidationError):
        _minimal(query="")


def test_channel_is_a_closed_set():
    with pytest.raises(ValidationError):
        _minimal(channel="telegram")


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        _minimal(user_phone="+919999999999")


def test_history_role_is_constrained():
    turn = _minimal(history=[{"role": "user", "content": "hi"}])
    assert turn.history[0].role == "user"
    with pytest.raises(ValidationError):
        _minimal(history=[{"role": "farmer", "content": "hi"}])


def test_turn_is_frozen():
    turn = _minimal()
    with pytest.raises(ValidationError):
        turn.query = "changed"


def test_location_and_geometry_parse():
    turn = _minimal(
        location={
            "region": "IN-GJ",
            "area": "Anand",
            "geometry": {"type": "Point", "coordinates": [72.93, 22.56]},
        }
    )
    assert turn.location.region == "IN-GJ"
    assert turn.location.geometry.coordinates == [72.93, 22.56]
