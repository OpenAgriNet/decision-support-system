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
from dss.orchestration.form_offer import find_forms, form_context_for
from tests.integration.orchestration.test_orchestrator import (
    _build,
    _ctx,
    _evidence,
    _FakeCompose,
    _FakePlan,
    _turn,
)
from tests.support.fakes import FakeSchemeCatalog

PMFBY = Scheme(code="PMFBY", name="Pradhan Mantri Fasal Bima Yojana")
PMFBY_SUPPORT = FormContext(scheme="PMFBY", category="Grievance", action="support")
WEATHER_ASK = Ask(
    subject_categories=SubjectCategory.WEATHER, interaction_type=InteractionType.OBSERVE
)
WEATHER_DESK = ProviderCapability(
    provider_id="imd",
    provider_name="IMD",
    capability="openagrinet:WeatherObservation",
    resource_id="res:imd:forecast",
    observed_categories=("Weather",),
)
GRIEVANCE_FORM = FormDefinition(context=PMFBY_SUPPORT, fields=())
GRIEVANCE_DESK = ProviderCapability(
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


def test_advice_names_no_form() -> None:
    assert form_context_for(_grievance_ask(InteractionType.ADVISE)) is None


def test_an_ask_without_a_scheme_names_no_form() -> None:
    assert form_context_for(_grievance_ask(InteractionType.ACT, scheme=None)) is None


def test_a_served_ask_the_registry_knows_gets_its_form() -> None:
    intent = Intent(asks=(_grievance_ask(InteractionType.ACT),), confidence=0.9)
    forms = _FakeForms({PMFBY_SUPPORT: GRIEVANCE_FORM})

    awaiting = find_forms(intent, _discovery({0: (GRIEVANCE_DESK,)}), forms)

    assert awaiting == {0: GRIEVANCE_FORM}


def test_an_ask_nobody_serves_gets_no_form() -> None:
    """No desk on this network: the unserved message, as for any other ask.
    The registry is not even asked."""

    intent = Intent(asks=(_grievance_ask(InteractionType.ACT),), confidence=0.9)
    forms = _FakeForms({PMFBY_SUPPORT: GRIEVANCE_FORM})

    awaiting = find_forms(intent, _discovery(), forms)

    assert awaiting == {}
    assert forms.asked == []


def test_a_served_ask_without_a_registry_form_is_the_planners() -> None:
    intent = Intent(asks=(_grievance_ask(InteractionType.ACT),), confidence=0.9)

    awaiting = find_forms(intent, _discovery({0: (GRIEVANCE_DESK,)}), _FakeForms({}))

    assert awaiting == {}


def test_only_the_form_ask_is_taken_from_a_mixed_turn() -> None:
    """ "What's the weather, and file my PMFBY grievance": the weather ask
    stays the planner's, the grievance ask is the form's."""

    intent = Intent(
        asks=(WEATHER_ASK, _grievance_ask(InteractionType.ACT)), confidence=0.9
    )
    discovery = _discovery({0: (WEATHER_DESK,), 1: (GRIEVANCE_DESK,)})

    awaiting = find_forms(
        intent, discovery, _FakeForms({PMFBY_SUPPORT: GRIEVANCE_FORM})
    )

    assert awaiting == {1: GRIEVANCE_FORM}


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

    assert plan.awaiting == {0: GRIEVANCE_FORM}
    assert events[-1].outcome.status is TurnStatus.REQUIRES_INPUT
