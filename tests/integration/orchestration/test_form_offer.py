"""Tier 3 — the form step: which form an ask names, and the offer a turn ends with."""

from __future__ import annotations

from dss.core.forms.models import FormContext
from dss.core.intent.models import Ask, InteractionType, Scheme, SubjectCategory
from dss.orchestration.form_offer import form_context_for

PMFBY = Scheme(code="PMFBY", name="Pradhan Mantri Fasal Bima Yojana")


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
