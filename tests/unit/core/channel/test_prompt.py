"""Tier 1 — laying evidence out for the composer.

Plain Python in, plain Python out: no LLM, no ports. The wording of the
answer is the model's job; this pins the layout the model reads, which is
what makes "one source per question" a rule it can follow.
"""

from __future__ import annotations

from dss.core.channel.prompt import render_evidence
from dss.core.planner.models import Evidence, Result, Source, SourceKind

PULSES = Source(id="1", name="PulsesGuidelines2025", kind=SourceKind.PROVIDER, url=None)
ICAR = Source(id="2", name="ICAR Agro-Advisories", kind=SourceKind.PROVIDER, url=None)
AGMARKNET = Source(id="3", name="Agmarknet", kind=SourceKind.PROVIDER, url=None)


def _evidence(*results: Result, sources: tuple[Source, ...]) -> Evidence:
    return Evidence(
        sources=sources,
        results=results,
        served=tuple(dict.fromkeys(result.ask_index for result in results)),
        failed=(),
        sufficient=bool(results),
    )


def test_one_sources_passages_are_gathered_under_one_heading() -> None:
    """Four passages from one document are one source, not four. Repeating
    the heading per passage made a single document look like four agreeing
    ones."""

    evidence = _evidence(
        Result(ask_index=0, source_id="1", data={"passage": "a"}),
        Result(ask_index=0, source_id="1", data={"passage": "b"}),
        sources=(PULSES,),
    )

    rendered = render_evidence(evidence)

    assert rendered.count("PulsesGuidelines2025") == 1
    assert '"passage": "a"' in rendered
    assert '"passage": "b"' in rendered


def test_each_source_answering_one_question_is_its_own_block() -> None:
    """The composer has to pick one of them, so it has to be able to see
    where one stops and the next starts."""

    evidence = _evidence(
        Result(ask_index=0, source_id="1", data={"passage": "a"}),
        Result(ask_index=0, source_id="2", data={"passage": "b"}),
        sources=(PULSES, ICAR),
    )

    rendered = render_evidence(evidence)

    assert "[1] PulsesGuidelines2025" in rendered
    assert "[2] ICAR Agro-Advisories" in rendered
    assert rendered.index("PulsesGuidelines2025") < rendered.index("ICAR")


def test_results_are_grouped_by_the_question_they_answer() -> None:
    """One source per question, not one per answer: a turn asking two things
    is served by two providers and says so."""

    evidence = _evidence(
        Result(ask_index=0, source_id="1", data={"passage": "a"}),
        Result(ask_index=1, source_id="3", data={"modal": 2200}),
        sources=(PULSES, AGMARKNET),
    )

    rendered = render_evidence(evidence)

    assert "Question 1" in rendered
    assert "Question 2" in rendered
    assert rendered.index("Question 1") < rendered.index("Question 2")


def test_nothing_retrieved_still_says_so() -> None:
    assert render_evidence(_evidence(sources=())) == "Nothing was retrieved."
