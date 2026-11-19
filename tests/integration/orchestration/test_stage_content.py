"""Tier 3 — the composer span names each source by both numbers."""

from __future__ import annotations

from dss.core.intent.models import (
    ClassifiedAsk,
    GuessedScheme,
    InteractionType,
    Scheme,
    SubjectCategory,
    UnlistedScheme,
)
from dss.core.planner.models import Evidence, Result, Source, SourceKind
from dss.core.provider_discovery.models import DiscoveryResult
from dss.core.shared.models import ProviderAction
from dss.orchestration.stage_content import (
    asks_text,
    asks_without_place,
    cited_sources_text,
    discovery_text,
    evidence_text,
)
from tests.support.asks import an_ask
from tests.support.evidence import build_evidence
from tests.support.providers import build_capability


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
    ask = an_ask(
        id=2,
        agriculture_subjects="crop insurance",
        subject_categories=SubjectCategory.GRIEVANCE,
        interaction_type=InteractionType.ACT,
        scheme=GuessedScheme(scheme=pmfby),
    )

    assert asks_text((ask,)) == "2. Grievance/act: crop insurance [PMFBY?]"


def test_unlisted_scheme_is_marked_in_the_trace() -> None:
    ask = an_ask(
        agriculture_subjects="crop insurance",
        subject_categories=SubjectCategory.GRIEVANCE,
        interaction_type=InteractionType.ACT,
        scheme=UnlistedScheme(name="XYZ Yojana"),
    )

    assert asks_text((ask,)) == (
        "0. Grievance/act: crop insurance [not listed: XYZ Yojana]"
    )


def test_the_action_is_shown_in_the_trace() -> None:
    """ "advise" beside a filing form explains nothing. The action does."""

    ask = an_ask(
        agriculture_subjects="crop insurance",
        subject_categories=SubjectCategory.GRIEVANCE,
        interaction_type=InteractionType.ADVISE,
        action=ProviderAction.SUPPORT,
    )

    assert asks_text((ask,)) == "0. Grievance/advise: crop insurance -> support"


def test_a_served_ask_is_named_by_its_id() -> None:
    """Ask 0 waits on a question, so only ask 2 was planned: the trace must
    not call it the first."""

    evidence = build_evidence(
        asks=(an_ask(id=2),),
        results=(Result(ask_id=2, source_id="1", data={}),),
    )

    assert evidence_text(evidence) == "served asks: 2\nno sources"


def test_a_discovered_ask_is_named_by_its_id() -> None:
    discovery = DiscoveryResult(
        answers={},
        capabilities={2: (build_capability(provider_name="IMD"),)},
        failures={},
        events=(),
    )

    assert discovery_text(discovery) == "2. offered by IMD"


def test_asks_for_the_log_leave_out_the_place() -> None:
    """The log is exported unfiltered, and a place can single a farmer out."""

    ask = ClassifiedAsk(
        agriculture_subjects="rain",
        subject_categories=SubjectCategory.WEATHER,
        interaction_type=InteractionType.OBSERVE,
        place_name="Kanha Chatti",
        action=ProviderAction.SUPPORT,
    )

    assert asks_without_place((ask,)) == ("Weather/observe: rain -> support",)
