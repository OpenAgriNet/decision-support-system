"""Tier 3 — the form step: which form an ask names, and the offer a turn ends with."""

from __future__ import annotations

from dss.core.forms.models import FormContext, FormDefinition
from dss.core.intent.models import (
    Ask,
    ClassifiedAsk,
    Intent,
    IntentClassification,
    InteractionType,
    Scheme,
    SubjectCategory,
)
from dss.core.provider_discovery.models import DiscoveryResult, ProviderCapability
from dss.core.shared.models import TurnStatus
from dss.orchestration.form_offer import (
    FormStep,
    ask_for_form,
    find_forms,
    form_context_for,
)
from tests.integration.orchestration.test_orchestrator import (
    _build,
    _ctx,
    _evidence,
    _FakeCompose,
    _FakePlan,
    _turn,
)
from tests.support.fakes import FakeSchemeCatalog
from tests.support.providers import build_capability

PMFBY = Scheme(code="PMFBY", name="Pradhan Mantri Fasal Bima Yojana")
PMFBY_SUPPORT = FormContext(scheme="PMFBY", category="Grievance", action="support")
WEATHER_ASK = Ask(
    subject_categories=SubjectCategory.WEATHER, interaction_type=InteractionType.OBSERVE
)
WEATHER_DESK = build_capability(
    provider_id="imd",
    provider_name="IMD",
    capability="openagrinet:WeatherObservation",
    resource_id="res:imd:forecast",
    observed_categories=("Weather",),
)
GRIEVANCE_FORM = FormDefinition(context=PMFBY_SUPPORT, fields=())
GRIEVANCE_DESK = build_capability(
    provider_id="pmfby",
    provider_name="PMFBY desk",
    capability="openagrinet:Grievance",
    resource_id="res:pmfby:grievance",
    observed_categories=("Grievance",),
)


class _FakeForms:
    def __init__(self, forms: dict[FormContext, FormDefinition]) -> None:
        self._forms = forms
        self.asked: list[FormContext] = []

    def find(self, context: FormContext) -> FormDefinition | None:
        self.asked.append(context)
        return self._forms.get(context)

    def contexts_for(self, category: str) -> tuple[FormContext, ...]:
        return tuple(context for context in self._forms if context.category == category)


def _discovery(
    capabilities: dict[int, tuple[ProviderCapability, ...]] | None = None,
) -> DiscoveryResult:
    return DiscoveryResult(
        answers={}, capabilities=capabilities or {}, failures={}, events=()
    )


def _grievance_ask(interaction: InteractionType, scheme: Scheme | None = PMFBY) -> Ask:
    return Ask(
        agriculture_subjects=PMFBY.name,
        subject_categories=SubjectCategory.GRIEVANCE,
        interaction_type=interaction,
        scheme=scheme,
    )


def test_filing_names_the_support_form() -> None:
    context = form_context_for(_grievance_ask(InteractionType.ACT))

    assert context == FormContext(
        scheme="PMFBY", category="Grievance", action="support"
    )


def test_a_lookup_names_the_status_form() -> None:
    context = form_context_for(_grievance_ask(InteractionType.OBSERVE))

    assert context == FormContext(scheme="PMFBY", category="Grievance", action="status")


def test_advice_names_the_support_form() -> None:
    """How to file a complaint comes with the form to file it."""

    context = form_context_for(_grievance_ask(InteractionType.ADVISE))

    assert context == PMFBY_SUPPORT


def test_an_ask_without_a_scheme_names_no_form() -> None:
    assert form_context_for(_grievance_ask(InteractionType.ACT, scheme=None)) is None


def test_a_served_ask_the_registry_knows_gets_its_form() -> None:
    intent = Intent(asks=(_grievance_ask(InteractionType.ACT),), confidence=0.9)
    forms = _FakeForms({PMFBY_SUPPORT: GRIEVANCE_FORM})

    step = find_forms(intent, _discovery({0: (GRIEVANCE_DESK,)}), forms)

    assert step.offered == {0: GRIEVANCE_FORM}
    assert step.settled == {0}


def test_an_ask_nobody_serves_gets_no_form() -> None:
    """No desk on this network: the unserved message, as for any other ask.
    The registry is not even asked."""

    intent = Intent(asks=(_grievance_ask(InteractionType.ACT),), confidence=0.9)
    forms = _FakeForms({PMFBY_SUPPORT: GRIEVANCE_FORM})

    step = find_forms(intent, _discovery(), forms)

    assert step == FormStep()
    assert forms.asked == []


def test_a_served_ask_without_a_registry_form_is_the_planners() -> None:
    intent = Intent(asks=(_grievance_ask(InteractionType.ACT),), confidence=0.9)

    step = find_forms(intent, _discovery({0: (GRIEVANCE_DESK,)}), _FakeForms({}))

    assert step == FormStep()


def test_only_the_form_ask_is_taken_from_a_mixed_turn() -> None:
    """ "What's the weather, and file my PMFBY grievance": the weather ask
    stays the planner's, the grievance ask is the form's."""

    intent = Intent(
        asks=(WEATHER_ASK, _grievance_ask(InteractionType.ACT)), confidence=0.9
    )
    discovery = _discovery({0: (WEATHER_DESK,), 1: (GRIEVANCE_DESK,)})

    step = find_forms(intent, discovery, _FakeForms({PMFBY_SUPPORT: GRIEVANCE_FORM}))

    assert step.offered == {1: GRIEVANCE_FORM}


_FORMS = {PMFBY_SUPPORT: GRIEVANCE_FORM}


def test_a_served_ask_without_a_scheme_misses_the_scheme() -> None:
    intent = Intent(
        asks=(_grievance_ask(InteractionType.ACT, scheme=None),), confidence=0.9
    )

    step = find_forms(intent, _discovery({0: (GRIEVANCE_DESK,)}), _FakeForms(_FORMS))

    assert step.missing == {0: ("scheme",)}
    assert step.offered == {}
    assert step.settled == {0}


def test_an_unserved_ask_without_a_scheme_is_no_form_ask() -> None:
    """Nobody serves the category here: the unserved reply, not a question
    that leads nowhere."""

    intent = Intent(
        asks=(_grievance_ask(InteractionType.ACT, scheme=None),), confidence=0.9
    )

    assert find_forms(intent, _discovery(), _FakeForms(_FORMS)) == FormStep()


def test_an_action_with_no_form_asks_for_nothing() -> None:
    intent = Intent(
        asks=(_grievance_ask(InteractionType.OBSERVE, scheme=None),), confidence=0.9
    )

    step = find_forms(intent, _discovery({0: (GRIEVANCE_DESK,)}), _FakeForms(_FORMS))

    assert step == FormStep()


def test_an_ask_outside_the_forms_categories_is_left_alone() -> None:
    intent = Intent(asks=(WEATHER_ASK,), confidence=0.9)

    step = find_forms(intent, _discovery({0: (WEATHER_DESK,)}), _FakeForms(_FORMS))

    assert step == FormStep()


def test_advice_gets_the_form_with_no_desk_and_nothing_left_to_call() -> None:
    """The grievance desk is never found for advice, on purpose: the form
    comes from the registry, and no planner run has anything to answer."""

    intent = Intent(asks=(_grievance_ask(InteractionType.ADVISE),), confidence=0.9)

    step = find_forms(intent, _discovery(), _FakeForms(_FORMS))

    assert step.offered == {0: GRIEVANCE_FORM}
    assert step.settled == {0}


def test_advice_a_provider_serves_still_goes_to_the_planner() -> None:
    """A Knowledge provider with steps answers the ask; the form is for the
    filing that follows."""

    intent = Intent(asks=(_grievance_ask(InteractionType.ADVISE),), confidence=0.9)

    step = find_forms(intent, _discovery({0: (GRIEVANCE_DESK,)}), _FakeForms(_FORMS))

    assert step.offered == {0: GRIEVANCE_FORM}
    assert step.settled == frozenset()


def test_advice_without_a_scheme_misses_the_scheme_even_when_served() -> None:
    intent = Intent(
        asks=(_grievance_ask(InteractionType.ADVISE, scheme=None),), confidence=0.9
    )

    step = find_forms(intent, _discovery({0: (GRIEVANCE_DESK,)}), _FakeForms(_FORMS))

    assert step.missing == {0: ("scheme",)}
    assert step.settled == {0}


def test_a_form_step_names_the_asks_it_holds() -> None:
    step = FormStep(offered={0: GRIEVANCE_FORM}, missing={2: ("scheme",)})

    assert step.asks == {0, 2}


def _grievance_classification() -> IntentClassification:
    return IntentClassification(
        asks=(
            ClassifiedAsk(
                agriculture_subjects="PMFBY",
                subject_categories=SubjectCategory.GRIEVANCE,
                interaction_type=InteractionType.ACT,
                scheme_code="PMFBY",
            ),
        ),
        confidence=0.9,
    )


async def test_the_planner_is_told_which_asks_await_a_form() -> None:
    """The seam: intent names the scheme on the ask, discovery serves it,
    the registry has the form, and the planner hears about it."""

    plan = _FakePlan(_evidence(results=(), awaiting={0: GRIEVANCE_FORM}))
    orch, _ = _build(
        intent=_grievance_classification(),
        discovery=_discovery({0: (GRIEVANCE_DESK,)}),
        plan=plan,
        compose=_FakeCompose("Please fill in the form below."),
        forms=_FakeForms({PMFBY_SUPPORT: GRIEVANCE_FORM}),
        scheme_catalog=FakeSchemeCatalog(PMFBY),
    )

    events = [
        event async for event in orch.run(_turn("file my PMFBY grievance"), _ctx())
    ]

    assert plan.form_step.offered == {0: GRIEVANCE_FORM}
    assert events[-1].outcome.status is TurnStatus.REQUIRES_INPUT


def test_a_submission_ask_names_the_form_it_came_from() -> None:
    """The ask built from a context must map back to that context, or the
    submission path and the offer path disagree about what a form is."""

    ask = ask_for_form(PMFBY_SUPPORT, scheme=PMFBY)

    assert ask.subject_categories is SubjectCategory.GRIEVANCE
    assert ask.interaction_type is InteractionType.ACT
    assert ask.scheme == PMFBY
    assert ask.place is None
    assert form_context_for(ask) == PMFBY_SUPPORT


def test_an_action_the_path_cannot_file_yields_no_ask() -> None:
    advice = FormContext(scheme="PMFBY", category="Grievance", action="advice")

    assert ask_for_form(advice, scheme=PMFBY) is None
