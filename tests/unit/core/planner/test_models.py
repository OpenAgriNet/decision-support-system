"""Tier 1 — the planner agent's evidence contract."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from pydantic import ValidationError

from dss.core.intent.models import Ask, InteractionType, SubjectCategory
from dss.core.planner.models import (
    AskEvidence,
    Evidence,
    Failure,
    Identity,
    Result,
    Skill,
    Source,
    SourceKind,
)


def test_source_carries_provenance() -> None:
    source = Source(id="1", name="Agmarknet", kind=SourceKind.PROVIDER, url=None)
    assert source.id == "1"
    assert source.name == "Agmarknet"
    assert source.kind == SourceKind.PROVIDER
    assert source.url is None


def test_source_kind_values() -> None:
    assert {k.value for k in SourceKind} == {"provider", "document", "tool"}


def test_source_is_frozen() -> None:
    source = Source(id="1", name="Agmarknet", kind=SourceKind.PROVIDER, url=None)
    with pytest.raises(ValidationError):
        source.name = "Someone else"


def test_source_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        Source(id="1", name="Agmarknet", kind=SourceKind.PROVIDER, url=None, extra="x")


def test_skill_carries_tool_names() -> None:
    skill = Skill(
        id="provider-invocation",
        domain="agriculture",
        description="Call providers to answer an ask.",
        guidance="Read the capability, build resourceAttributes, then call select.",
        tool_names=("select",),
    )
    assert skill.tool_names == ("select",)


def test_skill_is_frozen() -> None:
    skill = Skill(
        id="provider-invocation",
        domain="agriculture",
        description="d",
        guidance="g",
        tool_names=(),
    )
    with pytest.raises(ValidationError):
        skill.domain = "livestock"


def test_result_carries_ask_index_and_source() -> None:
    result = Result(ask_index=0, source_id="1", data={"modal_price": "2100"})
    assert result.ask_index == 0
    assert result.source_id == "1"
    assert result.data == {"modal_price": "2100"}


def test_result_is_frozen() -> None:
    result = Result(ask_index=0, source_id="1", data={})
    with pytest.raises(ValidationError):
        result.source_id = "2"


def test_failure_carries_capability_and_reason() -> None:
    failure = Failure(
        ask_index=0, capability="MandiPrice", reason="timeout", retryable=True
    )
    assert failure.ask_index == 0
    assert failure.capability == "MandiPrice"
    assert failure.reason == "timeout"
    assert failure.retryable is True


def test_failure_is_frozen() -> None:
    failure = Failure(
        ask_index=0, capability="MandiPrice", reason="timeout", retryable=True
    )
    with pytest.raises(ValidationError):
        failure.retryable = False


_ASK = Ask(
    subject_categories=SubjectCategory.MARKET, interaction_type=InteractionType.OBSERVE
)
_RESULT = Result(ask_index=0, source_id="1", data={"modal_price": "2100"})
_PROVIDER_FAILURE = Failure(
    ask_index=0, capability="MandiPrice", reason="timeout", retryable=True
)
_PLACE_FAILURE = Failure(
    ask_index=0, capability=None, reason="Xyzzy: place not found", retryable=False
)


def test_a_place_failure_is_not_a_provider_failure() -> None:
    """No provider was called, so none can be blamed."""

    assert _PROVIDER_FAILURE.is_provider_failure is True
    assert _PLACE_FAILURE.is_provider_failure is False


def test_an_ask_with_a_result_is_served_even_if_a_call_failed() -> None:
    ask_evidence = AskEvidence(
        ask=_ASK, results=(_RESULT,), failures=(_PROVIDER_FAILURE,)
    )

    assert ask_evidence.is_served is True


def test_an_ask_with_no_result_is_not_served() -> None:
    ask_evidence = AskEvidence(ask=_ASK, results=(), failures=(_PROVIDER_FAILURE,))

    assert ask_evidence.is_served is False


def test_an_ask_has_a_provider_failure_only_when_a_provider_was_called() -> None:
    place_only = AskEvidence(ask=_ASK, results=(), failures=(_PLACE_FAILURE,))
    both = AskEvidence(
        ask=_ASK, results=(), failures=(_PLACE_FAILURE, _PROVIDER_FAILURE)
    )

    assert place_only.has_provider_failure is False
    assert both.has_provider_failure is True


def test_ask_evidence_is_frozen() -> None:
    ask_evidence = AskEvidence(ask=_ASK, results=(), failures=())
    with pytest.raises(FrozenInstanceError):
        ask_evidence.results = (_RESULT,)  # type: ignore[misc]


_SERVED = AskEvidence(ask=_ASK, results=(_RESULT,), failures=())
_UNSERVED = AskEvidence(ask=_ASK, results=(), failures=())
_PLACE_FAILED = AskEvidence(ask=_ASK, results=(), failures=(_PLACE_FAILURE,))
_PROVIDER_FAILED = AskEvidence(ask=_ASK, results=(), failures=(_PROVIDER_FAILURE,))


def _evidence(*ask_evidences: AskEvidence) -> Evidence:
    return Evidence(sources=(), ask_evidences=ask_evidences)


def test_an_ask_no_result_served_is_reported() -> None:
    assert _evidence(_SERVED, _UNSERVED).unserved_asks == (_UNSERVED,)


def test_every_ask_served_leaves_no_gap() -> None:
    assert _evidence(_SERVED, _SERVED).unserved_asks == ()


def test_nothing_served_reports_every_ask() -> None:
    """Every ask is a gap — distinct from a turn with no asks, which has
    nothing to report."""

    evidence = _evidence(_UNSERVED, _PLACE_FAILED)

    assert evidence.unserved_asks == (_UNSERVED, _PLACE_FAILED)
    assert _evidence().unserved_asks == ()


def test_any_served_ask_is_a_result() -> None:
    assert _evidence(_UNSERVED, _SERVED).has_any_result is True
    assert _evidence(_UNSERVED, _PROVIDER_FAILED).has_any_result is False


def test_a_turn_has_a_provider_failure_only_when_a_provider_was_called() -> None:
    """A place failure called no provider, so it is not an outage and must not
    tell the farmer to retry."""

    assert _evidence(_SERVED, _PLACE_FAILED).has_provider_failure is False
    assert _evidence(_PLACE_FAILED, _PROVIDER_FAILED).has_provider_failure is True


def test_evidence_carries_sources_and_each_asks_evidence() -> None:
    source = Source(id="1", name="Agmarknet", kind=SourceKind.PROVIDER, url=None)

    evidence = Evidence(sources=(source,), ask_evidences=(_SERVED, _UNSERVED))

    assert evidence.sources == (source,)
    assert evidence.ask_evidences == (_SERVED, _UNSERVED)


def test_evidence_is_frozen() -> None:
    evidence = _evidence()
    with pytest.raises(FrozenInstanceError):
        evidence.sources = ()  # type: ignore[misc]


def test_identity_carries_name_persona_boundaries() -> None:
    identity = Identity(
        name="Kisan Mitra",
        persona="A calm, practical farm advisor.",
        boundaries="Never gives financial or legal advice.",
    )
    assert identity.name == "Kisan Mitra"
    assert identity.persona == "A calm, practical farm advisor."
    assert identity.boundaries == "Never gives financial or legal advice."


def test_identity_is_frozen() -> None:
    identity = Identity(name="Kisan Mitra", persona="p", boundaries="b")
    with pytest.raises(ValidationError):
        identity.name = "Someone else"
