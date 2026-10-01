"""Tier 1 — shaping `Evidence` + the composer's prose into a `ComposedAnswer`.

Plain Python in, plain Python out: no LLM, no ports. The prose is the model's
job; this only pins the structural mapping — sources copied across the core
boundary, and every one attached to the block so its citation resolves.
"""

from __future__ import annotations

from dss.core.channel.service import answer_from_evidence
from dss.core.planner.models import Evidence, Result, Source, SourceKind


def _evidence(*sources: Source) -> Evidence:
    return Evidence(
        sources=tuple(sources),
        results=tuple(
            Result(ask_index=0, source_id=s.id, data={"v": 1}) for s in sources
        ),
        served=(0,) if sources else (),
        failed=(),
        sufficient=bool(sources),
    )


def test_prose_becomes_the_single_block() -> None:
    answer = answer_from_evidence("Wheat is 2,275 Rs [1].", _evidence())

    assert len(answer.content) == 1
    assert answer.content[0].text == "Wheat is 2,275 Rs [1]."


def test_every_source_is_copied_to_the_wire_and_cited_by_the_block() -> None:
    source = Source(id="1", name="Agmarknet", kind=SourceKind.PROVIDER, url=None)

    answer = answer_from_evidence("Wheat is 2,275 Rs [1].", _evidence(source))

    # source crossed the core boundary intact
    assert [s.id for s in answer.sources] == ["1"]
    assert answer.sources[0].name == "Agmarknet"
    assert answer.sources[0].kind.value == "provider"
    # and the block cites it, so the annotation the mapping renders resolves
    assert answer.content[0].source_ids == ("1",)


def test_every_block_citation_names_a_listed_source() -> None:
    sources = (
        Source(id="1", name="Agmarknet", kind=SourceKind.PROVIDER, url=None),
        Source(id="2", name="IMD", kind=SourceKind.PROVIDER, url=None),
    )

    answer = answer_from_evidence("Wheat [1]. Rain [2].", _evidence(*sources))

    listed = {s.id for s in answer.sources}
    assert all(cited in listed for cited in answer.content[0].source_ids)
    assert answer.content[0].source_ids == ("1", "2")


def test_only_the_cited_source_is_listed() -> None:
    """One question is answered from one source. The other source was read
    and rejected, so listing it claims a provenance the answer does not
    have — and a farmer checking it finds a document the advice never
    came from."""

    sources = (
        Source(id="1", name="PulsesGuidelines2025", kind=SourceKind.PROVIDER, url=None),
        Source(id="2", name="ICAR Advisories", kind=SourceKind.PROVIDER, url=None),
    )

    answer = answer_from_evidence(
        "Sow masoor in late October [2].", _evidence(*sources)
    )

    assert [s.id for s in answer.sources] == ["2"]
    assert answer.sources[0].name == "ICAR Advisories"
    assert answer.content[0].source_ids == ("2",)


def test_several_questions_may_cite_several_sources() -> None:
    """The one-source rule is per question, so a reply answering two things
    keeps both sources it actually used."""

    sources = (
        Source(id="1", name="Agmarknet", kind=SourceKind.PROVIDER, url=None),
        Source(id="2", name="IMD", kind=SourceKind.PROVIDER, url=None),
    )

    answer = answer_from_evidence(
        "Onions are 2,275 Rs [1]. Rain is likely tomorrow [2].", _evidence(*sources)
    )

    assert [s.id for s in answer.sources] == ["1", "2"]


def test_a_citation_naming_no_listed_source_is_dropped() -> None:
    """A marker pointing at nothing is worse than no marker, so an invented
    number never reaches the wire."""

    source = Source(id="1", name="Agmarknet", kind=SourceKind.PROVIDER, url=None)

    answer = answer_from_evidence("Onions are 2,275 Rs [7].", _evidence(source))

    assert answer.sources == ()
    assert answer.content[0].source_ids == ()


def test_sources_are_listed_once_and_in_evidence_order() -> None:
    sources = (
        Source(id="1", name="Agmarknet", kind=SourceKind.PROVIDER, url=None),
        Source(id="2", name="IMD", kind=SourceKind.PROVIDER, url=None),
    )

    answer = answer_from_evidence("... [2] ... [1] ... [2].", _evidence(*sources))

    assert [s.id for s in answer.sources] == ["1", "2"]
