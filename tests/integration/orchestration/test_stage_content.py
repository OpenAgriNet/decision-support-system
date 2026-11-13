"""Tier 3 — the composer span names each source by both numbers."""

from __future__ import annotations

from dss.core.intent.models import (
    Ask,
    GuessedScheme,
    InteractionType,
    Scheme,
    SubjectCategory,
    UnlistedScheme,
)
from dss.core.planner.models import Evidence, Source, SourceKind
from dss.core.shared.models import ProviderAction
from dss.orchestration.stage_content import asks_text, cited_sources_text
from tests.support.evidence import build_evidence


def _evidence(*names: str) -> Evidence:
    return build_evidence(
        sources=tuple(
            Source(id=str(i), name=name, kind=SourceKind.PROVIDER, url=None)
            for i, name in enumerate(names, start=1)
        ),
    )


def test_each_source_shows_the_number_the_farmer_read() -> None:
    text = cited_sources_text(_evidence("PoP", "KAU Mango Guide"), {"2": "1"})

    assert text == "[1] PoP → not cited\n[2] KAU Mango Guide → [1]"


def test_no_sources_says_so() -> None:
    assert cited_sources_text(_evidence(), {}) == "no sources"


def test_guessed_scheme_is_marked_in_the_trace() -> None:
    """Whoever reads a trace must tell a guess from a scheme the farmer named."""

    pmfby = Scheme(code="PMFBY", name="Pradhan Mantri Fasal Bima Yojana")
    ask = Ask(
        agriculture_subjects="crop insurance",
        subject_categories=SubjectCategory.GRIEVANCE,
        interaction_type=InteractionType.ACT,
        scheme=GuessedScheme(scheme=pmfby),
    )

    assert asks_text((ask,)) == "1. Grievance/act: crop insurance [PMFBY?]"


def test_unlisted_scheme_is_marked_in_the_trace() -> None:
    ask = Ask(
        agriculture_subjects="crop insurance",
        subject_categories=SubjectCategory.GRIEVANCE,
        interaction_type=InteractionType.ACT,
        scheme=UnlistedScheme(name="XYZ Yojana"),
    )

    assert asks_text((ask,)) == (
        "1. Grievance/act: crop insurance [not listed: XYZ Yojana]"
    )


def test_the_action_is_shown_in_the_trace() -> None:
    """ "advise" beside a filing form explains nothing. The action does."""

    ask = Ask(
        agriculture_subjects="crop insurance",
        subject_categories=SubjectCategory.GRIEVANCE,
        interaction_type=InteractionType.ADVISE,
        action=ProviderAction.SUPPORT,
    )

    assert asks_text((ask,)) == "1. Grievance/advise: crop insurance -> support"
