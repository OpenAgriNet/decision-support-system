"""Tier 1 — which asks the turn never answered.

Separate from ``Evidence.sufficient``, which only says whether anything came
back at all. ``failed`` catches calls that errored; this catches asks the
model never attempted, which look identical on a bare Evidence.
"""

from __future__ import annotations

from dss.core.intent.models import Ask, Intent, InteractionType, SubjectCategory
from dss.core.planner.models import Evidence, Result, Source, SourceKind
from dss.core.planner.sufficiency import unserved_asks

PRICE_ASK = Ask(
    agriculture_subjects="paddy",
    subject_categories=SubjectCategory.MARKET,
    interaction_type=InteractionType.OBSERVE,
)

ADVISORY_ASK = Ask(
    agriculture_subjects="paddy",
    subject_categories=SubjectCategory.CROP,
    interaction_type=InteractionType.ADVISE,
)

SOURCE = Source(id="1", name="Agmarknet", kind=SourceKind.PROVIDER, url=None)


def _evidence(*served: int) -> Evidence:
    return Evidence(
        sources=(SOURCE,),
        results=tuple(
            Result(ask_index=ask_index, source_id="1", data={}) for ask_index in served
        ),
        served=served,
        failed=(),
        sufficient=bool(served),
    )


def test_an_ask_no_result_served_is_reported() -> None:
    intent = Intent(asks=(PRICE_ASK, ADVISORY_ASK), confidence=0.9)

    assert unserved_asks(_evidence(0), intent=intent) == (1,)


def test_every_ask_served_leaves_no_gap() -> None:
    intent = Intent(asks=(PRICE_ASK, ADVISORY_ASK), confidence=0.9)

    assert unserved_asks(_evidence(0, 1), intent=intent) == ()


def test_nothing_served_reports_every_ask() -> None:
    """The turn produced no results at all. Every ask is a gap — distinct from
    a turn with no asks, which has nothing to report."""

    intent = Intent(asks=(PRICE_ASK, ADVISORY_ASK), confidence=0.9)

    assert unserved_asks(_evidence(), intent=intent) == (0, 1)
