"""Tier 1 — laying evidence out for the composer, and the place fallback
each result/failure can lean on.

Plain Python in, plain Python out: no LLM, no ports. The wording of the
answer is the model's job; this pins the layout the model reads, which is
what makes "one source per question" a rule it can follow.
"""

from __future__ import annotations

from dss.core.channel.prompt import render_evidence, system_prompt, user_prompt
from dss.core.forms.models import (
    FieldValue,
    FormContext,
    FormDefinition,
    FormReference,
    FormSubmission,
)
from dss.core.intent.models import (
    Ask,
    GuessedScheme,
    InteractionType,
    PlaceSource,
    ResolvedPlace,
    Scheme,
    SubjectCategory,
)
from dss.core.planner.models import (
    AskEvidence,
    Evidence,
    Failure,
    Identity,
    Result,
    Source,
    SourceKind,
)
from dss.core.settlement.models import (
    Ambiguous,
    AskSlot,
    CannotServe,
    Missing,
    NeedsClarification,
    Slot,
    Unresolved,
    UnresolvedReason,
    Unservable,
)
from dss.core.shared.language import LanguageTag
from dss.core.shared.models import ConversationMessage, Geometry, TextQuery, UserTurn
from dss.ports.prompts import PromptIdentifier
from tests.support.evidence import build_evidence
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


def _evidence(*results: Result, sources: tuple[Source, ...]) -> Evidence:
    return build_evidence(
        sources=sources,
        results=results,
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


def test_a_result_is_labelled_with_its_asks_resolved_place() -> None:
    evidence = build_evidence(
        asks=(_ask(_place("Pune")),),
        sources=(Source(id="1", name="Agmarknet", kind=SourceKind.PROVIDER, url=None),),
        results=(Result(ask_index=0, source_id="1", data={"modal": 2200}),),
    )

    rendered = render_evidence(evidence)

    assert "Pune" in rendered


def test_the_label_names_the_place_above_so_two_rampurs_are_told_apart() -> None:
    rampur = ResolvedPlace(
        name="Rampur",
        within=("India", "Himachal Pradesh"),
        geometry=Geometry(coordinates=[77.63, 31.45]),
        source=PlaceSource.NAMED,
    )
    evidence = build_evidence(
        asks=(_ask(rampur),),
        sources=(Source(id="1", name="IMD", kind=SourceKind.PROVIDER, url=None),),
        results=(Result(ask_index=0, source_id="1", data={"rain": 2}),),
    )

    rendered = render_evidence(evidence)

    assert "— about Rampur, Himachal Pradesh\n" in rendered


def test_a_place_with_nothing_above_it_is_labelled_with_its_name_alone() -> None:
    """A device point sent with an area name has no chain: no dangling comma."""

    anand = ResolvedPlace(
        name="Anand",
        within=(),
        geometry=Geometry(coordinates=[72.95, 22.56]),
        source=PlaceSource.ASSERTED_GEOMETRY,
    )
    evidence = build_evidence(
        asks=(_ask(anand),),
        sources=(Source(id="1", name="IMD", kind=SourceKind.PROVIDER, url=None),),
        results=(Result(ask_index=0, source_id="1", data={"rain": 2}),),
    )

    rendered = render_evidence(evidence)

    assert "— about Anand\n" in rendered


def test_a_failure_is_labelled_with_its_asks_resolved_place() -> None:
    evidence = build_evidence(
        asks=(_ask(_place("Anand")),),
        sources=(),
        results=(),
        failures=(
            Failure(
                ask_index=0,
                capability="openagrinet:WeatherObservation",
                reason="timeout",
                retryable=True,
            ),
        ),
    )

    rendered = render_evidence(evidence)

    assert "Anand" in rendered


def test_an_ask_awaiting_a_form_is_rendered_as_such() -> None:
    """Neither a result nor a gap: the composer must point at the form, not
    apologise for it."""

    pmfby = Scheme(code="PMFBY", name="Pradhan Mantri Fasal Bima Yojana")
    ask = Ask(
        agriculture_subjects=pmfby.name,
        subject_categories=SubjectCategory.GRIEVANCE,
        interaction_type=InteractionType.ACT,
        scheme=pmfby,
    )
    form = FormDefinition(
        context=FormContext(scheme="PMFBY", category="Grievance", action="support"),
        fields=(),
    )

    rendered = render_evidence(build_evidence(asks=(ask,), awaiting={0: form}))

    assert "Awaits a form" in rendered
    assert "Pradhan Mantri Fasal Bima Yojana grievance form" in rendered
    assert "Nothing was retrieved" not in rendered


def test_a_form_beside_a_guess_names_the_forms_scheme() -> None:
    """Only a named scheme has a name to show. The form's own code is the
    one we are sure of."""

    pmfby = Scheme(code="PMFBY", name="Pradhan Mantri Fasal Bima Yojana")
    ask = Ask(
        subject_categories=SubjectCategory.GRIEVANCE,
        interaction_type=InteractionType.ACT,
        scheme=GuessedScheme(scheme=pmfby),
    )
    form = FormDefinition(
        context=FormContext(scheme="PMFBY", category="Grievance", action="support"),
        fields=(),
    )

    rendered = render_evidence(build_evidence(asks=(ask,), awaiting={0: form}))

    assert "PMFBY grievance form" in rendered


def _waiting_on(*open_slots: Slot) -> Evidence:
    rain = Ask(
        subject_categories=SubjectCategory.WEATHER,
        interaction_type=InteractionType.OBSERVE,
    )
    outcome = NeedsClarification(open_slots=open_slots)
    return Evidence(sources=(), ask_evidences=(AskEvidence(ask=rain, outcome=outcome),))


def test_an_ask_waiting_on_the_farmer_asks_one_question() -> None:
    """Only the first open slot: one question per turn."""

    rendered = render_evidence(
        _waiting_on(Slot(AskSlot.PLACE, Missing()), Slot("village", Missing()))
    )

    assert "Ask the farmer" in rendered
    assert "place" in rendered
    assert "village" not in rendered
    assert "Nothing was retrieved" not in rendered


def test_a_missing_action_asks_new_or_old() -> None:
    """Asked by its name, it would read "which action?", which means nothing
    to a farmer."""

    rendered = render_evidence(_waiting_on(Slot(AskSlot.ACTION, Missing())))

    assert "raise something new" in rendered
    assert "already raised" in rendered
    assert "Which action" not in rendered


def test_an_ambiguous_slot_points_at_the_choices() -> None:
    """Code lists the choices exactly, below the reply; the composer only
    asks which one."""

    choices = ("Aurangabad MH", "Aurangabad BR")

    rendered = render_evidence(
        _waiting_on(Slot(AskSlot.PLACE, Ambiguous("Aurangabad", choices)))
    )

    assert "list below this reply" in rendered
    assert "Aurangabad MH" not in rendered


def test_one_choice_is_a_confirm() -> None:
    """No list follows one choice, so the brief must not point at one."""

    pmfby = Scheme(code="PMFBY", name="Pradhan Mantri Fasal Bima Yojana")

    rendered = render_evidence(
        _waiting_on(Slot(AskSlot.SCHEME, Ambiguous(pmfby.name, (pmfby,))))
    )

    assert "Do they mean 'Pradhan Mantri Fasal Bima Yojana'?" in rendered
    assert "list below this reply" not in rendered


def test_an_unknown_value_is_named_and_asked_again() -> None:
    rendered = render_evidence(
        _waiting_on(
            Slot(
                AskSlot.PLACE,
                Unresolved(value="Xyzzy", reason=UnresolvedReason.NOT_FOUND),
            )
        )
    )

    assert "Xyzzy" in rendered
    assert "could not find" in rendered


def test_a_too_broad_value_asks_for_a_smaller_one() -> None:
    rendered = render_evidence(
        _waiting_on(
            Slot(
                AskSlot.PLACE,
                Unresolved(value="Maharashtra", reason=UnresolvedReason.TOO_BROAD),
            )
        )
    )

    assert "Maharashtra" in rendered
    assert "too big" in rendered


def test_an_ask_we_cannot_serve_says_why() -> None:
    complaint = Ask(
        subject_categories=SubjectCategory.GRIEVANCE,
        interaction_type=InteractionType.ACT,
    )
    outcome = CannotServe(
        slot=Slot("scheme", Unservable(reason="PMFBY has no complaint form"))
    )
    evidence = Evidence(
        sources=(), ask_evidences=(AskEvidence(ask=complaint, outcome=outcome),)
    )

    rendered = render_evidence(evidence)

    assert "Cannot serve" in rendered
    assert "PMFBY has no complaint form" in rendered


def test_a_place_failure_does_not_blame_a_provider() -> None:
    """No provider was called for Xyzzy. "Could not reach a provider" would
    have the composer tell the farmer a service is down."""

    evidence = build_evidence(
        asks=(_ask(_place("Pune")), _ask()),
        sources=(),
        results=(),
        failures=(
            Failure(
                ask_index=1,
                capability=None,
                reason="Xyzzy: place not found",
                retryable=False,
            ),
        ),
    )

    rendered = render_evidence(evidence)

    assert "Xyzzy: place not found" in rendered
    assert "Could not reach a provider" not in rendered


def test_a_device_point_with_no_name_labels_nothing() -> None:
    """A device point with no area has no name. "about " with nothing after it
    is noise the composer might try to fill."""

    evidence = build_evidence(
        asks=(_ask(_place("")),),
        sources=(Source(id="1", name="IMD", kind=SourceKind.PROVIDER, url=None),),
        results=(Result(ask_index=0, source_id="1", data={"rain": "none"}),),
    )

    assert "about" not in render_evidence(evidence)


def test_no_place_resolved_labels_nothing() -> None:
    """`place=None` — nothing named, no fallback, or the ask needed none —
    is not a fact worth stating, so no label is added."""

    evidence = build_evidence(
        asks=(_ask(place=None),),
        sources=(Source(id="1", name="Agmarknet", kind=SourceKind.PROVIDER, url=None),),
        results=(Result(ask_index=0, source_id="1", data={"modal": 2200}),),
    )

    rendered = render_evidence(evidence)

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
        input=TextQuery(text="q"),
        session_id="s1",
        transaction_id="t1",
        source_lang="mr",
        target_lang="mr",
        channel="web",
    )

    system_prompt(identity, turn=turn, prompts=prompts)

    [(identifier, lang, kwargs)] = prompts.calls
    assert identifier is PromptIdentifier.COMPOSER
    assert lang == LanguageTag("mr")
    assert kwargs == {
        "name": "Kisan Mitra",
        "persona": "a calm advisor.",
        "boundaries": "Never gives legal advice.",
        "target_lang": LanguageTag("mr"),
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
        input=TextQuery(text="2"),
        session_id="s1",
        transaction_id="t1",
        source_lang="en",
        target_lang="en",
        channel="web",
        history=history,
    )
    evidence = build_evidence()

    prompt = user_prompt(evidence, turn=turn)

    assert "<BEGIN CONVERSATION>" in prompt
    assert "Good to hear." in prompt
    assert "What is the weather in Rampur?" in prompt
    assert "Which Rampur? 1. … 2. …" in prompt
    assert "My cotton looks fine." not in prompt


def test_a_form_turn_is_described_without_its_values() -> None:
    """The composer must know what the farmer did; it must never see what
    they typed, so there is nothing to repeat."""

    turn = UserTurn[FormSubmission](
        input=FormSubmission(
            form_id="offer-1",
            context=FormContext(scheme="PMFBY", category="Grievance", action="support"),
            submission_id="submission-1",
            values=(FieldValue(name="applicantMobile", value="9876543210"),),
        ),
        session_id="s1",
        transaction_id="t1",
        source_lang="en",
        target_lang="en",
        channel="web",
    )

    prompt = user_prompt(build_evidence(), turn=turn)

    assert "PMFBY/Grievance/support" in prompt
    assert "9876543210" not in prompt


def test_the_composer_sees_a_form_in_history_as_its_fixed_line() -> None:
    offered = ConversationMessage(
        role="assistant",
        text="Please fill in the form below.",
        reference=FormReference(
            form_id="offer-1",
            context=FormContext(scheme="PMFBY", category="Grievance", action="support"),
        ),
    )
    turn = UserTurn[TextQuery](
        input=TextQuery(text="Was it filed?"),
        session_id="s1",
        transaction_id="t1",
        source_lang="en",
        target_lang="en",
        channel="web",
        history=[offered],
    )

    prompt = user_prompt(build_evidence(), turn=turn)

    assert "assistant: Please fill in the form below. (a form was offered)" in prompt
    assert "offer-1" not in prompt
