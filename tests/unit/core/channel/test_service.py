"""Tier 1 — the deterministic channel shaping.

The prose-writing composer lives in `core/channel/compose.py` (it needs an
LLM); what stays here is the fixed no-match reply. `answer_from_evidence` has
its own test alongside this one.
"""

from __future__ import annotations

from dss.config.clarification_text_loader import load_clarification_text
from dss.core.channel.models import ClarificationText
from dss.core.channel.service import (
    NO_MATCH_TEXT,
    answer_for_session_busy,
    answer_for_unserved_asks,
    no_match_answer,
    pick_lists,
)
from dss.core.intent.models import (
    PlaceSource,
    ResolvedPlace,
    UnresolvedPlace,
)
from dss.core.planner.models import AskEvidence, Evidence
from dss.core.settlement.models import (
    Ambiguous,
    AskSlot,
    NeedsClarification,
    Slot,
)
from dss.core.shared.models import Geometry, TextBlock
from dss.ports.area_lookup import AreaMatch
from tests.support.asks import an_ask

_TEXT = ClarificationText(
    more_places_hint="Not in this list? Tell me the area it is in.",
    no_provider_for="I could not find a source that answers this for {place} yet.",
    no_provider="I could not find a source that answers this yet.",
    unknown_form="I do not know that form.",
    form_has_errors="Please check the fields marked below.",
    field_required="{label} is needed.",
    field_pattern="{label} is not in the right format.",
    field_min_length="{label} must be at least {min} characters.",
    field_max_length="{label} must be at most {max} characters.",
    field_not_an_option="{label} must be one of the given choices.",
    field_duplicate="{label} was sent more than once.",
    field_unknown="{label} is not part of this form.",
    not_filed="Your complaint was not filed. Please try again later.",
    not_filed_rejected="The desk could not take your complaint as sent.",
    status_not_checked="We could not check your complaint's status right now.",
    status_not_found="No complaint was found for that ticket number and phone.",
    session_busy="Another message from you is still being handled. Try again.",
    already_filed="This form was already sent.",
    no_init_pending="Nothing is waiting for this response.",
    init_not_sent="We could not reach the provider to start this.",
    init_rejected="The provider did not accept these details.",
    init_response_needed="Enter the one-time password the provider sent you.",
    went_wrong="Something went wrong on our side.",
    form_cannot_complete="This form cannot be completed right now.",
    saved_details_unusable="We could not use your saved details.",
    field_recheck="Check this value.",
    init_response_refused="The provider did not accept that response.",
    init_response_not_sent="Nothing was sent. Try the same response again.",
    outcome_unknown="We could not confirm whether this went through.",
    case_not_found="No case was found for these details.",
)


def test_no_match_answer_has_one_block_and_no_sources() -> None:
    answer = no_match_answer()

    assert len(answer.content) == 1
    assert answer.content[0].text == NO_MATCH_TEXT
    # nothing was consulted, so nothing is cited
    assert answer.sources == ()
    assert answer.content[0].source_ids == ()


_BILASPUR_HP = AreaMatch(
    name="Bilaspur",
    region="IN-HP",
    within=("India", "Himachal Pradesh"),
    geometry=Geometry(coordinates=[76.75, 31.33]),
)
_BILASPUR_CT = AreaMatch(
    name="Bilaspur",
    region="IN-CT",
    within=("India", "Chhattisgarh"),
    geometry=Geometry(coordinates=[82.15, 22.09]),
)
_WHICH_BILASPUR = Ambiguous("Bilaspur", (_BILASPUR_HP, _BILASPUR_CT))


def _waiting_on(*open_slots: Slot) -> Evidence:
    outcome = NeedsClarification(open_slots=open_slots)
    return Evidence(ask_evidences=(AskEvidence(ask=an_ask(), outcome=outcome),))


def test_an_ambiguous_place_gets_a_numbered_pick_list() -> None:
    """Code writes the list, so the farmer's "2" matches what was shown."""

    evidence = _waiting_on(Slot(AskSlot.PLACE, _WHICH_BILASPUR))

    [block] = pick_lists(evidence, _TEXT)

    assert block.text == "1. Bilaspur, Himachal Pradesh\n2. Bilaspur, Chhattisgarh"


def test_two_asks_share_one_pick_list() -> None:
    """Two asks stuck on one name are one thing for the farmer to pick."""

    which_bilaspur = NeedsClarification(
        open_slots=(Slot(AskSlot.PLACE, _WHICH_BILASPUR),)
    )
    evidence = Evidence(
        ask_evidences=(
            AskEvidence(ask=an_ask(), outcome=which_bilaspur),
            AskEvidence(ask=an_ask(), outcome=which_bilaspur),
        ),
    )

    assert len(pick_lists(evidence, _TEXT)) == 1


def _place(name: str, *within: str) -> AreaMatch:
    return AreaMatch(
        name=name,
        region="IN-XX",
        within=("India", *within),
        geometry=Geometry(coordinates=[80.0, 26.0]),
    )


def _pick_list(*candidates: AreaMatch) -> list[str]:
    typed_name = candidates[0].name
    evidence = _waiting_on(Slot(AskSlot.PLACE, Ambiguous(typed_name, candidates)))
    [block] = pick_lists(evidence, _TEXT)
    return block.text.splitlines()


def test_each_choice_shows_the_part_of_its_chain_that_differs() -> None:
    """Both Ashtis are in Maharashtra, so the state tells them apart for no
    one. The district does."""

    assert _pick_list(
        _place("Ashti", "Maharashtra", "Wardha"),
        _place("Ashti", "Maharashtra", "Beed"),
    ) == ["1. Ashti, Wardha", "2. Ashti, Beed"]


def test_a_single_guess_gets_no_list() -> None:
    """One choice is a yes or no. The composer's question names it."""

    guess = _place("Kanha Chatti", "Jharkhand", "Chatra")
    evidence = _waiting_on(
        Slot(AskSlot.PLACE, Ambiguous("Kanha Chatti, Chatra", (guess,)))
    )

    assert pick_lists(evidence, _TEXT) == ()


def test_each_choice_stops_where_it_stands_apart_from_every_other() -> None:
    """Three Akbarpurs, two in Uttar Pradesh. The state is enough for the
    Bihar one. The two in Uttar Pradesh only differ by district."""

    assert _pick_list(
        _place("Akbarpur", "Bihar", "Nawada"),
        _place("Akbarpur", "Uttar Pradesh", "Kanpur Dehat"),
        _place("Akbarpur", "Uttar Pradesh", "Ambedkar Nagar"),
    ) == [
        "1. Akbarpur, Bihar",
        "2. Akbarpur, Kanpur Dehat",
        "3. Akbarpur, Ambedkar Nagar",
    ]


def test_a_state_among_the_choices_has_its_own_line() -> None:
    """ "Kashmir" is two villages and a word in a state. The state is listed
    by its full name, so the farmer can pick it."""

    state = AreaMatch(
        name="Jammu and Kashmir",
        region="IN",
        within=("India",),
        geometry=Geometry(coordinates=[75.16, 33.66]),
        is_region=True,
    )

    assert _pick_list(
        _place("Kashmir", "Rajasthan", "Sheo Tehsil"),
        _place("Kashmir", "Himachal Pradesh", "Karsog"),
        state,
    ) == [
        "1. Kashmir, Rajasthan",
        "2. Kashmir, Himachal Pradesh",
        "3. Jammu and Kashmir, India",
    ]


def test_a_long_list_is_grouped_one_level_up() -> None:
    """Six Rampurs, two in each of three states. Six lines are too many to
    read. The farmer picks a state first, and the next turn narrows further."""

    assert _pick_list(
        _place("Rampur", "Uttar Pradesh", "Moradabad"),
        _place("Rampur", "Uttar Pradesh", "Rampur"),
        _place("Rampur", "Himachal Pradesh", "Shimla"),
        _place("Rampur", "Himachal Pradesh", "Kullu"),
        _place("Rampur", "Odisha", "Cuttack"),
        _place("Rampur", "Odisha", "Puri"),
    ) == [
        "1. Rampur, Uttar Pradesh",
        "2. Rampur, Himachal Pradesh",
        "3. Rampur, Odisha",
    ]


def test_a_grouped_list_is_cut_at_five_and_says_how_to_find_the_rest() -> None:
    """Seven Rampurs in seven states: list five, then tell the farmer whose
    place is not shown how to get to it."""

    states = ["UP", "HP", "OD", "BR", "JH", "MP", "RJ"]

    assert _pick_list(*(_place("Rampur", state) for state in states)) == [
        "1. Rampur, UP",
        "2. Rampur, HP",
        "3. Rampur, OD",
        "4. Rampur, BR",
        "5. Rampur, JH",
        "Not in this list? Tell me the area it is in.",
    ]


def test_grouped_list_uses_the_typed_name() -> None:
    """No place is called "North", so six that only start with it come back.
    No one of their names fits every group. What the farmer typed does."""

    states = ["Goa", "Sikkim", "Tripura", "Delhi", "Meghalaya", "West Bengal"]
    starting_with_north = tuple(_place(f"North {state}", state) for state in states)
    evidence = _waiting_on(
        Slot(AskSlot.PLACE, Ambiguous(value="North", choices=starting_with_north))
    )

    [block] = pick_lists(evidence, _TEXT)

    assert block.text.splitlines()[:2] == ["1. North, Goa", "2. North, Sikkim"]


_PUNE = ResolvedPlace(
    name="Pune",
    within=("IN-MH",),
    geometry=Geometry(coordinates=[73.85, 18.52]),
    source=PlaceSource.NAMED,
)


def test_no_provider_names_the_place() -> None:
    """Nobody serves the ask here. Say so, rather than read as out of scope."""

    answer = answer_for_unserved_asks((an_ask(place=_PUNE),), _TEXT)

    assert answer.content[0].text == (
        "I could not find a source that answers this for Pune yet."
    )


def test_no_provider_without_a_place_name() -> None:
    """A device point can arrive with no name to show."""

    device_point = _PUNE.model_copy(
        update={"name": "", "source": PlaceSource.ASSERTED_GEOMETRY}
    )

    answer = answer_for_unserved_asks((an_ask(place=device_point),), _TEXT)

    assert answer.content[0].text == _TEXT.no_provider


def test_no_provider_names_each_place_once() -> None:
    """ "Wheat and onion price in Pune" is one thing the farmer can act on."""

    answer = answer_for_unserved_asks((an_ask(place=_PUNE), an_ask(place=_PUNE)), _TEXT)

    assert answer.content[0].text == (
        "I could not find a source that answers this for Pune yet."
    )


def test_an_ask_with_no_place_nobody_serves_still_gets_an_answer() -> None:
    """A form's ask names no place; the farmer must not read an empty bubble."""

    answer = answer_for_unserved_asks([an_ask(place=None)], _TEXT)

    (block,) = answer.content
    assert block.text == _TEXT.no_provider


def test_the_busy_answer_is_the_configured_text() -> None:
    text = load_clarification_text()

    answer = answer_for_session_busy(text)

    assert answer.content == (TextBlock(text=text.session_busy),)


def test_an_unclear_place_nobody_serves_still_gets_an_answer() -> None:
    """A complaint names Xyzzy in passing. No question follows it now, so the
    farmer must still hear that nobody serves it."""

    xyzzy = an_ask(place=UnresolvedPlace(unresolved_name="Xyzzy"))

    answer = answer_for_unserved_asks([xyzzy], _TEXT)

    (block,) = answer.content
    assert block.text == _TEXT.no_provider
