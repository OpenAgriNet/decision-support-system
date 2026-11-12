"""Tier 1 — an ask's own slots: whether each is needed, and its state."""

from __future__ import annotations

from collections.abc import Iterable

import pytest

from dss.core.intent.models import (
    AmbiguousPlace,
    Ask,
    GuessedScheme,
    InteractionType,
    PlaceSource,
    ResolvedPlace,
    Scheme,
    SubjectCategory,
    UnclearAction,
    UnlistedScheme,
    UnresolvedPlace,
)
from dss.core.settlement.ask_slots import ask_slot_state, frame_for, needed_slots
from dss.core.settlement.models import (
    Ambiguous,
    AskNeeds,
    AskSlot,
    Frame,
    Missing,
    Resolved,
    Slot,
    SlotState,
    Unresolved,
    UnresolvedReason,
    Unservable,
)
from dss.core.shared.models import Geometry, ProviderAction
from dss.ports.area_lookup import AreaMatch

PUNE = ResolvedPlace(
    name="Pune",
    within=("IN-MH",),
    geometry=Geometry(coordinates=[73.85, 18.52]),
    source=PlaceSource.NAMED,
)


def _needs(
    *,
    place: bool = False,
    schemes: Iterable[str] = (),
    actions: Iterable[ProviderAction] = (),
) -> AskNeeds:
    return AskNeeds(
        needs_place=place,
        schemes_with_a_form=frozenset(schemes),
        actions_with_a_form=frozenset(actions),
    )


def test_place_slot_when_needed() -> None:
    assert needed_slots(_needs(place=True)) == (AskSlot.PLACE,)


def test_no_slot_when_nothing_needed() -> None:
    assert needed_slots(_needs()) == ()


def test_scheme_slot_when_category_has_forms() -> None:
    assert needed_slots(_needs(schemes={"PMFBY"})) == (AskSlot.SCHEME,)


def test_scheme_is_asked_before_place() -> None:
    needs = _needs(place=True, schemes={"PMFBY"})

    assert needed_slots(needs) == (AskSlot.SCHEME, AskSlot.PLACE)


def test_action_slot_when_there_is_a_choice() -> None:
    assert needed_slots(_needs(actions=ProviderAction)) == (AskSlot.ACTION,)


def test_no_action_slot_for_one_kind_of_form() -> None:
    """With one kind of form there is nothing to choose."""

    assert needed_slots(_needs(actions={ProviderAction.SUPPORT})) == ()


def test_action_is_asked_before_scheme_and_place() -> None:
    """It is the bigger fork, and it stops a second complaint early."""

    needs = _needs(place=True, schemes={"PMFBY"}, actions=ProviderAction)

    assert needed_slots(needs) == (AskSlot.ACTION, AskSlot.SCHEME, AskSlot.PLACE)


def _place_of(
    place: ResolvedPlace | AmbiguousPlace | UnresolvedPlace | None,
) -> SlotState:
    ask = Ask(
        subject_categories=SubjectCategory.WEATHER,
        interaction_type=InteractionType.OBSERVE,
        place=place,
    )
    return ask_slot_state(ask, AskSlot.PLACE, _needs(place=True))


def test_resolved_place() -> None:
    assert _place_of(PUNE) == Resolved(PUNE)


def test_no_place_given() -> None:
    assert _place_of(None) == Missing()


def _aurangabad(region: str) -> AreaMatch:
    return AreaMatch(
        name="Aurangabad",
        region=region,
        within=("India",),
        geometry=Geometry(coordinates=[75.3, 19.9]),
    )


def test_ambiguous_place() -> None:
    candidates = (_aurangabad("IN-MH"), _aurangabad("IN-BR"))
    place = AmbiguousPlace(unresolved_name="Aurangabad", candidates=candidates)

    assert _place_of(place) == Ambiguous("Aurangabad", candidates)


def test_lone_place_guess_is_named() -> None:
    """One candidate is a confirm, so the value is the name we guessed, not
    the one the farmer typed."""

    kanha_chatti = AreaMatch(
        name="Kanha Chatti",
        region="IN-JH",
        within=("India",),
        geometry=Geometry(coordinates=[85.0, 24.2]),
    )
    place = AmbiguousPlace(unresolved_name="Kanha", candidates=(kanha_chatti,))

    assert _place_of(place) == Ambiguous("Kanha Chatti", (kanha_chatti,))


def test_unknown_place_is_not_found() -> None:
    place = UnresolvedPlace(unresolved_name="Xyzzy")

    assert _place_of(place) == Unresolved(
        value="Xyzzy", reason=UnresolvedReason.NOT_FOUND
    )


def test_whole_state_is_too_broad() -> None:
    """Named by the area's own spelling, so the question reads "which place
    in Maharashtra?", whatever the farmer typed."""

    place = UnresolvedPlace(unresolved_name="maharashtra", region="Maharashtra")

    assert _place_of(place) == Unresolved(
        value="Maharashtra", reason=UnresolvedReason.TOO_BROAD
    )


PMFBY = Scheme(code="PMFBY", name="Pradhan Mantri Fasal Bima Yojana")
PM_KISAN = Scheme(code="PMKISAN", name="PM Kisan Samman Nidhi")


def _scheme_of(
    scheme: Scheme | GuessedScheme | UnlistedScheme | None,
) -> SlotState:
    """Only PMFBY has a form."""

    ask = Ask(
        subject_categories=SubjectCategory.GRIEVANCE,
        interaction_type=InteractionType.ACT,
        scheme=scheme,
    )
    return ask_slot_state(ask, AskSlot.SCHEME, _needs(schemes={"PMFBY"}))


def test_named_scheme() -> None:
    assert _scheme_of(PMFBY) == Resolved(PMFBY)


def test_named_scheme_without_a_form() -> None:
    """Asking again would go in a circle: the farmer has no other answer."""

    assert _scheme_of(PM_KISAN) == Unservable(
        reason="PM Kisan Samman Nidhi has no grievance form."
    )


def test_guessed_scheme_is_one_choice() -> None:
    """One choice is a confirm. The farmer typed no scheme name, so the
    value is the guessed scheme's name: the name the question is about."""

    assert _scheme_of(GuessedScheme(scheme=PMFBY)) == Ambiguous(
        value="Pradhan Mantri Fasal Bima Yojana", choices=(PMFBY,)
    )


def test_guess_without_a_form_is_not_confirmed() -> None:
    """A yes would only lead to "we cannot serve this". The open question
    lets the farmer name a scheme we do serve."""

    assert _scheme_of(GuessedScheme(scheme=PM_KISAN)) == Missing()


def test_unlisted_scheme_ends_the_ask() -> None:
    """Asking again would go in a circle: the farmer has no other answer."""

    assert _scheme_of(UnlistedScheme(name="XYZ Yojana")) == Unservable(
        reason="XYZ Yojana is not a scheme we know."
    )


def test_no_scheme_given() -> None:
    assert _scheme_of(None) == Missing()


def _action_of(action: ProviderAction | UnclearAction | None) -> SlotState:
    ask = Ask(
        subject_categories=SubjectCategory.GRIEVANCE,
        interaction_type=InteractionType.ACT,
        action=action,
    )
    return ask_slot_state(ask, AskSlot.ACTION, _needs(actions=ProviderAction))


def test_clear_action() -> None:
    assert _action_of(ProviderAction.SUPPORT) == Resolved(ProviderAction.SUPPORT)


def test_unclear_action_is_asked() -> None:
    """Never guessed: a wrong guess files a second complaint, or asks for a
    ticket the farmer does not have."""

    assert _action_of(UnclearAction.UNCLEAR) == Missing()


def test_unknown_slot_fails_at_once() -> None:
    """A slot with no case would come back as None and fail far from here."""

    with pytest.raises(AssertionError):
        ask_slot_state(_weather_at(None), "village", _needs())  # type: ignore[arg-type]


def _weather_at(place: ResolvedPlace | None) -> Ask:
    return Ask(
        subject_categories=SubjectCategory.WEATHER,
        interaction_type=InteractionType.OBSERVE,
        place=place,
    )


def test_frame_holds_each_needed_slot() -> None:
    frame = frame_for(_weather_at(PUNE), _needs(place=True))

    assert frame == Frame(slots=(Slot(AskSlot.PLACE, Resolved(PUNE)),))


def test_frame_leaves_out_unneeded_slots() -> None:
    """A place said in passing is never asked about."""

    assert frame_for(_weather_at(PUNE), _needs()) == Frame(slots=())
