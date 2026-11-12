"""Tier 1 — which facts an ask needs, from its category's forms."""

from __future__ import annotations

from dss.core.forms.models import FormContext
from dss.core.intent.models import (
    Ask,
    InteractionType,
    SubjectCategory,
    UnclearAction,
)
from dss.core.settlement.needs import build_ask_needs
from dss.core.shared.models import ProviderAction

SUPPORT = ProviderAction.SUPPORT
STATUS = ProviderAction.STATUS


def _form(scheme: str, action: str) -> FormContext:
    return FormContext(scheme=scheme, category="Grievance", action=action)


def _grievance(
    action: ProviderAction | UnclearAction | None,
    interaction: InteractionType = InteractionType.ACT,
) -> Ask:
    return Ask(
        subject_categories=SubjectCategory.GRIEVANCE,
        interaction_type=interaction,
        action=action,
    )


def test_an_unclear_action_counts_every_form() -> None:
    """The farmer's answer may still pick the one form a scheme has, so no
    scheme is turned away before they give it."""

    forms = [_form("PMFBY", "support"), _form("PM-KISAN", "status")]

    needs = build_ask_needs(
        _grievance(UnclearAction.UNCLEAR), needs_place=False, forms=forms
    )

    assert needs.schemes_with_a_form == {"PMFBY", "PM-KISAN"}
    assert needs.actions_with_a_form == {SUPPORT, STATUS}


def test_a_known_action_counts_only_its_forms() -> None:
    """PM-KISAN has a status form only, so a new complaint about it cannot be
    filed. Both kinds of form still exist for the category."""

    forms = [_form("PMFBY", "support"), _form("PM-KISAN", "status")]

    needs = build_ask_needs(_grievance(SUPPORT), needs_place=False, forms=forms)

    assert needs.schemes_with_a_form == {"PMFBY"}
    assert needs.actions_with_a_form == {SUPPORT, STATUS}


def test_a_plain_question_needs_no_form() -> None:
    """ "How long does a grievance take?" wants an answer. With no forms
    counted, it is asked nothing about one."""

    forms = [_form("PMFBY", "support"), _form("PMFBY", "status")]

    needs = build_ask_needs(
        _grievance(None, InteractionType.ADVISE), needs_place=False, forms=forms
    )

    assert needs.schemes_with_a_form == frozenset()
    assert needs.actions_with_a_form == frozenset()


def test_a_step_no_form_files_is_not_a_choice() -> None:
    """A pack can declare a step, such as init, that the farmer never picks."""

    forms = [_form("PMFBY", "support"), _form("PM-KISAN", "init")]

    needs = build_ask_needs(
        _grievance(UnclearAction.UNCLEAR), needs_place=False, forms=forms
    )

    assert needs.schemes_with_a_form == {"PMFBY"}
    assert needs.actions_with_a_form == {SUPPORT}


def test_wording_does_not_change_what_is_needed() -> None:
    """Only the action the model read counts, not whether the farmer put it
    as a question or a request."""

    forms = [_form("PMFBY", "support"), _form("PMFBY", "status")]

    asked_as_a_question = build_ask_needs(
        _grievance(SUPPORT, InteractionType.ADVISE), needs_place=True, forms=forms
    )
    asked_as_a_request = build_ask_needs(
        _grievance(SUPPORT, InteractionType.ACT), needs_place=True, forms=forms
    )

    assert asked_as_a_question == asked_as_a_request
    assert asked_as_a_question.needs_place is True
