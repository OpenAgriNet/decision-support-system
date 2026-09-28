"""Tier 1 — the composer's prompt: the question, the retrieved data, and the
place fallback each result/failure can lean on.

No framework, no network — plain strings in, plain strings out.
"""

from __future__ import annotations

from dss.core.channel.prompt import SYSTEM_PROMPT, render_evidence
from dss.core.intent.models import (
    Ask,
    Intent,
    InteractionType,
    PlaceSource,
    ResolvedPlace,
    SubjectCategory,
)
from dss.core.planner.models import Evidence, Failure, Result, Source, SourceKind
from dss.core.shared.models import Geometry


def _place(name: str) -> ResolvedPlace:
    return ResolvedPlace(
        name=name,
        within=("IN-MH",),
        geometry=Geometry(coordinates=[73.85, 18.52]),
        source=PlaceSource.NAMED,
    )


def _ask(place: ResolvedPlace | None = None) -> Ask:
    return Ask(
        subject_categories=SubjectCategory.WEATHER,
        interaction_type=InteractionType.OBSERVE,
        place=place,
    )


def test_a_result_is_labelled_with_its_asks_resolved_place() -> None:
    intent = Intent(asks=(_ask(_place("Pune")),))
    evidence = Evidence(
        sources=(Source(id="1", name="Agmarknet", kind=SourceKind.PROVIDER, url=None),),
        results=(Result(ask_index=0, source_id="1", data={"modal": 2200}),),
        served=(0,),
        failed=(),
        sufficient=True,
    )

    rendered = render_evidence(evidence, intent)

    assert "Pune" in rendered


def test_a_failure_is_labelled_with_its_asks_resolved_place() -> None:
    intent = Intent(asks=(_ask(_place("Anand")),))
    evidence = Evidence(
        sources=(),
        results=(),
        served=(),
        failed=(
            Failure(
                ask_index=0,
                capability="openagrinet:WeatherObservation",
                reason="timeout",
                retryable=True,
            ),
        ),
        sufficient=False,
    )

    rendered = render_evidence(evidence, intent)

    assert "Anand" in rendered


def test_no_place_resolved_labels_nothing() -> None:
    """`place=None` — nothing named, no fallback, or the ask needed none —
    is not a fact worth stating, so no label is added."""

    intent = Intent(asks=(_ask(place=None),))
    evidence = Evidence(
        sources=(Source(id="1", name="Agmarknet", kind=SourceKind.PROVIDER, url=None),),
        results=(Result(ask_index=0, source_id="1", data={"modal": 2200}),),
        served=(0,),
        failed=(),
        sufficient=True,
    )

    rendered = render_evidence(evidence, intent)

    assert "about" not in rendered.lower()


def test_system_prompt_says_which_place_the_answer_is_about() -> None:
    assert "which place the answer is about" in SYSTEM_PROMPT.lower()


def test_system_prompt_prefers_the_datas_own_place() -> None:
    """The provider's own data is more precise than the district centroid the
    turn resolved around — a mandi price already names the actual market."""

    prompt = SYSTEM_PROMPT.lower()
    assert "data itself names a place" in prompt
    assert "more precise" in prompt
