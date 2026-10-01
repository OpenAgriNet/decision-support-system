"""Tier 1 — the composer's prompt: the question, the retrieved data, and the
place fallback each result/failure can lean on.

No framework, no network — plain strings in, plain strings out.
"""

from __future__ import annotations

from dss.core.channel.prompt import SYSTEM_PROMPT, render_evidence, user_prompt
from dss.core.intent.models import (
    Ask,
    Intent,
    InteractionType,
    PlaceSource,
    ResolvedPlace,
    SubjectCategory,
)
from dss.core.planner.models import Evidence, Failure, Result, Source, SourceKind
from dss.core.shared.models import ConversationMessage, Geometry, UserTurn


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


def test_the_label_names_the_place_above_so_two_rampurs_are_told_apart() -> None:
    rampur = ResolvedPlace(
        name="Rampur",
        within=("India", "Himachal Pradesh"),
        geometry=Geometry(coordinates=[77.63, 31.45]),
        source=PlaceSource.NAMED,
    )
    intent = Intent(asks=(_ask(rampur),))
    evidence = Evidence(
        sources=(Source(id="1", name="IMD", kind=SourceKind.PROVIDER, url=None),),
        results=(Result(ask_index=0, source_id="1", data={"rain": 2}),),
        served=(0,),
        failed=(),
        sufficient=True,
    )

    rendered = render_evidence(evidence, intent)

    assert "— about Rampur, Himachal Pradesh\n" in rendered


def test_a_place_with_nothing_above_it_is_labelled_with_its_name_alone() -> None:
    """A device point sent with an area name has no chain: no dangling comma."""

    anand = ResolvedPlace(
        name="Anand",
        within=(),
        geometry=Geometry(coordinates=[72.95, 22.56]),
        source=PlaceSource.ASSERTED_GEOMETRY,
    )
    intent = Intent(asks=(_ask(anand),))
    evidence = Evidence(
        sources=(Source(id="1", name="IMD", kind=SourceKind.PROVIDER, url=None),),
        results=(Result(ask_index=0, source_id="1", data={"rain": 2}),),
        served=(0,),
        failed=(),
        sufficient=True,
    )

    rendered = render_evidence(evidence, intent)

    assert "— about Anand\n" in rendered


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


def test_a_place_failure_does_not_blame_a_provider() -> None:
    """No provider was called for Xyzzy. "Could not reach a provider" would
    have the composer tell the farmer a service is down."""

    intent = Intent(asks=(_ask(_place("Pune")), _ask()))
    evidence = Evidence(
        sources=(),
        results=(),
        served=(),
        failed=(
            Failure(
                ask_index=1,
                capability=None,
                reason="Xyzzy: place not found",
                retryable=False,
            ),
        ),
        sufficient=False,
    )

    rendered = render_evidence(evidence, intent)

    assert "Xyzzy: place not found" in rendered
    assert "Could not reach a provider" not in rendered


def test_a_device_point_with_no_name_labels_nothing() -> None:
    """A device point with no area has no name. "about " with nothing after it
    is noise the composer might try to fill."""

    intent = Intent(asks=(_ask(_place("")),))
    evidence = Evidence(
        sources=(Source(id="1", name="IMD", kind=SourceKind.PROVIDER, url=None),),
        results=(Result(ask_index=0, source_id="1", data={"rain": "none"}),),
        served=(0,),
        failed=(),
        sufficient=True,
    )

    assert "about" not in render_evidence(evidence, intent)


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


def test_composer_sees_last_three_messages() -> None:
    """A reply like "2" means nothing alone. The composer sees the last three
    messages, wrapped as data, and nothing older."""

    history = [
        ConversationMessage(role="user", text="My cotton looks fine."),
        ConversationMessage(role="assistant", text="Good to hear."),
        ConversationMessage(role="user", text="What is the weather in Rampur?"),
        ConversationMessage(role="assistant", text="Which Rampur? 1. … 2. …"),
    ]
    turn = UserTurn(
        original_query="2",
        enriched_query="2",
        session_id="s1",
        transaction_id="t1",
        source_lang="en",
        target_lang="en",
        channel="web",
        history=history,
    )
    evidence = Evidence(sources=(), results=(), served=(), failed=(), sufficient=False)

    prompt = user_prompt(evidence, Intent(), turn=turn)

    assert "<BEGIN CONVERSATION>" in prompt
    assert "Good to hear." in prompt
    assert "What is the weather in Rampur?" in prompt
    assert "Which Rampur? 1. … 2. …" in prompt
    assert "My cotton looks fine." not in prompt
