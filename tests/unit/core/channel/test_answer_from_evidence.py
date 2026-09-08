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

    answer = answer_from_evidence("...", _evidence(*sources))

    listed = {s.id for s in answer.sources}
    assert all(cited in listed for cited in answer.content[0].source_ids)
    assert answer.content[0].source_ids == ("1", "2")
