"""Tier 1 — laying evidence out for the composer, and the place fallback
each result/failure can lean on.

Plain Python in, plain Python out: no LLM, no ports. The wording of the
answer is the model's job; this pins the layout the model reads, which is
what makes "one source per question" a rule it can follow.
"""

from __future__ import annotations

from dss.core.channel.prompt import render_evidence, system_prompt, user_prompt
from dss.core.intent.models import (
    Ask,
    Intent,
    InteractionType,
    PlaceSource,
    ResolvedPlace,
    SubjectCategory,
)
from dss.core.planner.models import (
    Evidence,
    Failure,
    Identity,
    Result,
    Source,
    SourceKind,
)
from dss.core.shared.models import ConversationMessage, Geometry, UserTurn
from tests.support.fakes import FakePromptProvider

PULSES = Source(id="1", name="PulsesGuidelines2025", kind=SourceKind.PROVIDER, url=None)
ICAR = Source(id="2", name="ICAR Agro-Advisories", kind=SourceKind.PROVIDER, url=None)
AGMARKNET = Source(id="3", name="Agmarknet", kind=SourceKind.PROVIDER, url=None)


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


# Two asks naming no place: the layout tests below are about grouping, not
# the place label, so nothing adds a label to their headings.
_NO_PLACES = Intent(asks=(_ask(), _ask()))


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

    rendered = render_evidence(evidence, _NO_PLACES)

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

    rendered = render_evidence(evidence, _NO_PLACES)

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

    rendered = render_evidence(evidence, _NO_PLACES)

    assert "Question 1" in rendered
    assert "Question 2" in rendered
    assert rendered.index("Question 1") < rendered.index("Question 2")


def test_nothing_retrieved_still_says_so() -> None:
    assert (
        render_evidence(_evidence(sources=()), _NO_PLACES) == "Nothing was retrieved."
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


def test_system_prompt_hands_the_identity_and_language_to_the_template() -> None:
    """The prompt's text is config (prompts/composer/, pinned in
    tests/unit/config/test_shipped_prompts.py); what core owns is asking for
    the COMPOSER prompt in the turn's target language with the identity's
    fields. `target_lang` is both the lookup language and a template value —
    the answer's language is the turn's, whatever language the prompt is in."""

    prompts = FakePromptProvider()
    identity = Identity(
        name="Kisan Mitra",
        persona="a calm advisor.",
        boundaries="Never gives legal advice.",
    )
    turn = UserTurn(
        original_query="q",
        enriched_query="q",
        session_id="s1",
        transaction_id="t1",
        source_lang="mr",
        target_lang="mr",
        channel="web",
    )

    system_prompt(identity, turn=turn, prompts=prompts)

    [(identifier, lang, kwargs)] = prompts.calls
    assert identifier == "COMPOSER"
    assert lang == "mr"
    assert kwargs == {
        "name": "Kisan Mitra",
        "persona": "a calm advisor.",
        "boundaries": "Never gives legal advice.",
        "target_lang": "mr",
    }


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
