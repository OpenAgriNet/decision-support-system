"""Tier 1 — the Planner Agent (POC) evidence contract (issue #10)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dss.core.planner.models import Evidence, Failure, Result, Skill, Source, SourceKind


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
    failure = Failure(capability="MandiPrice", reason="timeout", retryable=True)
    assert failure.capability == "MandiPrice"
    assert failure.reason == "timeout"
    assert failure.retryable is True


def test_failure_is_frozen() -> None:
    failure = Failure(capability="MandiPrice", reason="timeout", retryable=True)
    with pytest.raises(ValidationError):
        failure.retryable = False


def test_evidence_assembles_sources_results_and_gaps() -> None:
    source = Source(id="1", name="Agmarknet", kind=SourceKind.PROVIDER, url=None)
    result = Result(ask_index=0, source_id="1", data={"modal_price": "2100"})
    failure = Failure(capability="KnowledgeAdvisory", reason="timeout", retryable=True)

    evidence = Evidence(
        sources=(source,),
        results=(result,),
        served=(0,),
        failed=(failure,),
        sufficient=True,
    )

    assert evidence.sources == (source,)
    assert evidence.results == (result,)
    assert evidence.served == (0,)
    assert evidence.failed == (failure,)
    assert evidence.sufficient is True


def test_evidence_is_frozen() -> None:
    evidence = Evidence(sources=(), results=(), served=(), failed=(), sufficient=False)
    with pytest.raises(ValidationError):
        evidence.sufficient = True
