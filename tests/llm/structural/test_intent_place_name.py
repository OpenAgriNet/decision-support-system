"""Tier 5 — does a real model fill `ClassifiedAsk.place_name`?

Structural only: the assertion is that whatever the model extracted *resolves*
against the shipped area index, never that it produced a particular string.

The prompt asks for the place in English, so the Marathi query is the case that
matters — the index carries English names only, and a model that echoes "पुणे"
verbatim would leave every non-English turn without a spatial filter while the
English one passed.

Skipped unless `.env` carries a model key, like `tests/e2e/test_onion_price.py`:
the suite stays green on a checkout with no credentials, and this runs
deliberately against whatever `DSS_INTENT_MODEL` the deployment binds.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import dotenv_values

from dss.adapters.area_lookup.csv_lookup import CsvAreaLookup
from dss.adapters.llm.pydantic_ai_provider import PydanticAILLMProvider
from dss.config.clarification_text_loader import load_clarification_text
from dss.config.settings import DEFAULT_AREA_CSV, Settings
from dss.core.channel.service import answer_for_unplaced_asks
from dss.core.intent.models import (
    ClassifiedAsk,
    IntentClassification,
    InteractionType,
    ResolvedPlace,
    SubjectCategory,
)
from dss.core.intent.service import classify_intent
from dss.core.location.service import resolve_places
from dss.core.shared.models import ConversationMessage, UserTurn
from dss.entrypoint.composition import _resolve_model
from dss.observability.stages import Stage
from tests.support.live_model import missing_model_key

_DOTENV = Path(__file__).parents[3] / ".env"


def _dotenv_values() -> dict[str, str]:
    """`.env`, read *without* touching `os.environ`.

    Importing this module must not reconfigure the rest of the run: a
    module-level `load_dotenv()` leaks the developer's real settings into every
    later `Settings()` in the same pytest process, which silently broke the
    defaults tests in `tests/unit/config/`.
    """

    return {name: value for name, value in dotenv_values(_DOTENV).items() if value}


# The key the configured intent model needs, from the shell or `.env` — the
# two places the run reads it from.
_MISSING = missing_model_key(
    Settings().intent_model, {**os.environ, **_dotenv_values()}
)
pytestmark = pytest.mark.skipif(
    _MISSING is not None,
    reason=f"live model test — DSS_INTENT_MODEL needs {_MISSING}, in .env or exported",
)


@pytest.fixture(autouse=True)
def _live_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """`.env` into the environment for the duration of one test.

    `_resolve_model` and the Azure SDK read AZURE_OPENAI_* from `os.environ`
    directly, so it has to be there — `monkeypatch` puts it back afterwards.
    """

    for name, value in _dotenv_values().items():
        monkeypatch.setenv(name, value)


# The real shipped index, not a fixture: the point is that a model's output
# lands on one of these areas.
LOOKUP = CsvAreaLookup.load(DEFAULT_AREA_CSV)


def _live_llm() -> PydanticAILLMProvider:
    # Plain `Settings()` so .env applies: the deployment's DSS_INTENT_MODEL
    # decides the provider (`azure:...` or `openai:...`). Constructing it with
    # overrides would fall back to the field default and call OpenAI with
    # whatever key happens to be set.
    settings = Settings()
    return PydanticAILLMProvider(
        _resolve_model(settings.intent_model, settings),
        name="intent-classifier",
        stage=Stage.INTENT,
        temperature=settings.intent_temperature,
        timeout=settings.intent_timeout_seconds,
        retries=settings.intent_retries,
    )


def _turn(
    query: str,
    *,
    source_lang: str = "en",
    history: list[ConversationMessage] | None = None,
) -> UserTurn:
    return UserTurn(
        original_query=query,
        enriched_query=query,
        session_id="s1",
        transaction_id="t1",
        source_lang=source_lang,
        target_lang=source_lang,
        channel="web",
        history=history or [],
    )


@pytest.mark.parametrize(
    ("query", "source_lang"),
    [
        ("I am from Pune, what is the wheat price?", "en"),
        # Transliteration, not translation: "from Pune" in Marathi.
        ("मी पुण्याहून आहे, गव्हाचा भाव काय आहे?", "mr"),
    ],
    ids=["english", "marathi"],
)
async def test_a_named_place_resolves_to_one_district(
    query: str, source_lang: str
) -> None:
    classification = await classify_intent(
        _turn(query, source_lang=source_lang), _live_llm()
    )

    assert classification.asks, "the model returned no asks"
    place_name = classification.asks[0].place_name
    assert place_name is not None, "the model named no place"
    # Exactly one: a name resolving to none would leave the turn without a
    # spatial filter, and one resolving to several is the ambiguous case.
    matches = LOOKUP.resolve(place_name)
    assert len(matches) == 1, f"{place_name!r} resolved to {len(matches)}"
    assert classification.asks[0].place_from_history is False, (
        "a place said this turn was flagged as carried"
    )


async def test_a_place_named_earlier_carries_to_a_follow_up() -> None:
    """ "And tomorrow?" names no place. The one the farmer said a turn ago
    should come back, not null — else the answer is for the device's point.
    """

    history = [
        ConversationMessage(role="user", text="What is the weather in Pune?"),
        ConversationMessage(role="assistant", text="Pune: clear skies, 31°C today."),
    ]

    classification = await classify_intent(
        _turn("And tomorrow?", history=history), _live_llm()
    )

    assert classification.asks, "the model returned no asks"
    place_name = classification.asks[0].place_name
    assert place_name is not None, "the model dropped the earlier place"
    matches = LOOKUP.resolve(place_name)
    assert len(matches) == 1, f"{place_name!r} resolved to {len(matches)}"
    assert classification.asks[0].place_from_history is True, (
        "a carried place was not flagged as carried"
    )


async def test_two_places_in_one_sentence_become_two_asks() -> None:
    """One subject, two places. A model that folds them into one ask answers
    for one city and silently drops the other.
    """

    classification = await classify_intent(
        _turn("What is the weather in Pune and Mumbai?"), _live_llm()
    )

    place_names = [ask.place_name for ask in classification.asks]
    assert len(place_names) == 2, f"expected two asks, got {classification!r}"
    resolved = set()
    for place_name in place_names:
        assert place_name is not None, f"an ask named no place: {classification!r}"
        matches = LOOKUP.resolve(place_name)
        assert len(matches) == 1, f"{place_name!r} resolved to {len(matches)}"
        resolved.add(matches[0].name)
    assert len(resolved) == 2, f"both asks resolved to {resolved}"


async def test_a_place_with_its_state_stays_one_ask() -> None:
    """The state narrows Pune; it is not a second place. Two asks here would
    fetch Maharashtra's weather as well as Pune's.
    """

    classification = await classify_intent(
        _turn("What is the weather in Pune, Maharashtra?"), _live_llm()
    )

    assert len(classification.asks) == 1, f"expected one ask, got {classification!r}"
    place_name = classification.asks[0].place_name
    assert place_name is not None, f"the ask named no place: {classification!r}"
    matches = LOOKUP.resolve(place_name)
    assert len(matches) == 1, f"{place_name!r} resolved to {len(matches)}"


async def test_no_place_named_leaves_it_none() -> None:
    """The prompt says not to guess a place from the crop, the language, or the
    subject. Untested, that instruction is only a hope — a model that invents
    "Pune" here would send every advisory turn to one arbitrary district.
    """

    classification = await classify_intent(
        _turn("How do I treat potato blight?"), _live_llm()
    )

    assert classification.asks, "the model returned no asks"
    place_name = classification.asks[0].place_name
    assert place_name is None, f"invented {place_name!r}"


async def test_a_conversation_with_no_place_carries_none_forward() -> None:
    """Carry-forward tells the model to look back. This proves looking back
    finds nothing when nobody said a place — the relaxation's price.
    """

    history = [
        ConversationMessage(role="user", text="My potato leaves have dark spots."),
        ConversationMessage(
            role="assistant", text="That sounds like late blight on potato."
        ),
    ]

    classification = await classify_intent(
        _turn("When should I spray?", history=history), _live_llm()
    )

    assert classification.asks, "the model returned no asks"
    place_name = classification.asks[0].place_name
    assert place_name is None, f"invented {place_name!r}"


async def test_one_place_covering_two_asks_is_on_both() -> None:
    """Code lends a sibling's place only when the turn has no location, so
    the model must put Pune on the rain ask itself."""

    classification = await classify_intent(
        _turn("What is the wheat price and will it rain in Pune?"), _live_llm()
    )

    assert len(classification.asks) == 2, f"expected two asks, got {classification!r}"
    for ask in classification.asks:
        assert ask.place_name is not None, f"an ask lost Pune: {classification!r}"
        assert len(LOOKUP.resolve(ask.place_name)) == 1


async def test_here_is_left_for_the_device() -> None:
    """ "Here" is where the farmer is. Filling it with the sibling's Pune
    would answer the rain for the wrong place."""

    classification = await classify_intent(
        _turn("Onion price in Pune, and will it rain here?"), _live_llm()
    )

    rain = [
        ask
        for ask in classification.asks
        if ask.subject_categories is SubjectCategory.WEATHER
    ]
    assert len(rain) == 1, f"expected one weather ask, got {classification!r}"
    assert rain[0].place_name is None, f"'here' became {rain[0].place_name!r}"


async def test_a_place_only_the_assistant_said_is_not_carried() -> None:
    """Lasalgaon is the market the answer came from, not where the farmer is.
    Carrying it would answer the follow-up for the wrong place."""

    history = [
        ConversationMessage(role="user", text="What is the onion price?"),
        ConversationMessage(
            role="assistant", text="At Lasalgaon APMC, onion is 1,800 a quintal."
        ),
    ]

    classification = await classify_intent(
        _turn("And tomorrow?", history=history), _live_llm()
    )

    assert classification.asks, "the model returned no asks"
    place_name = classification.asks[0].place_name
    assert place_name is None, f"carried the assistant's {place_name!r}"


# The question our own code sends when a name matches several places, verbatim
# (`core/channel/service.py`, `clarification-text.yaml`). The reply turn works
# only if the model copies one of these lines exactly.
_RAMPUR_LINES = [
    "Rampur, Uttar Pradesh",
    "Rampur, Himachal Pradesh",
    "Rampur, Odisha",
]
_RAMPUR_QUESTION = "Which Rampur?\n" + "\n".join(
    f"{number}. {line}" for number, line in enumerate(_RAMPUR_LINES, start=1)
)


def _asked_which_rampur() -> list[ConversationMessage]:
    return [
        ConversationMessage(role="user", text="What is the weather in Rampur?"),
        ConversationMessage(role="assistant", text=_RAMPUR_QUESTION),
    ]


@pytest.mark.parametrize(
    "reply",
    [
        "Himachal",
        "2",
        "the one in Himachal Pradesh",
        "Rampur himachal",
        "HP",
        "हिमाचल",
    ],
    ids=["state", "number", "the-one-in", "name-and-state", "abbreviation", "hindi"],
)
async def test_a_reply_to_which_place_finishes_the_first_question(
    reply: str,
) -> None:
    """The farmer's reply means nothing alone. The model must answer the
    question asked before, with the picked line copied as listed — the code
    that narrows the place matches the part after the comma exactly."""

    classification = await classify_intent(
        _turn(reply, history=_asked_which_rampur()), _live_llm()
    )

    assert len(classification.asks) == 1, f"expected one ask, got {classification!r}"
    ask = classification.asks[0]
    assert ask.subject_categories is SubjectCategory.WEATHER, (
        f"the first question was lost: {classification!r}"
    )
    assert ask.place_name == "Rampur, Himachal Pradesh"


async def test_a_pick_from_the_grouped_list_keeps_the_name() -> None:
    """A long list is grouped one level up: the lines are the states. Picking
    one gives the name and that part, which narrows the matches."""

    history = [
        ConversationMessage(role="user", text="What is the weather in Rampur?"),
        ConversationMessage(
            role="assistant",
            text=(
                "Rampur is in several places. Which one:\n"
                "1. Rampur, Uttar Pradesh\n2. Rampur, Himachal Pradesh\n"
                "3. Rampur, Odisha\n4. Rampur, Bihar\n5. Rampur, Jharkhand\n"
                "Not in this list? Tell me the area it is in."
            ),
        ),
    ]

    classification = await classify_intent(
        _turn("Uttar Pradesh", history=history), _live_llm()
    )

    assert len(classification.asks) == 1, f"expected one ask, got {classification!r}"
    assert classification.asks[0].place_name == "Rampur, Uttar Pradesh"


async def test_a_new_question_after_the_list_is_not_a_reply() -> None:
    """The farmer ignored the question and asked something else. The old
    Rampur must not leak into it."""

    classification = await classify_intent(
        _turn("What is the wheat price in Pune?", history=_asked_which_rampur()),
        _live_llm(),
    )

    assert len(classification.asks) == 1, f"expected one ask, got {classification!r}"
    ask = classification.asks[0]
    assert ask.subject_categories is SubjectCategory.MARKET
    assert ask.place_name == "Pune"


async def test_a_follow_up_after_the_pick_keeps_the_picked_place() -> None:
    """ "And tomorrow?" after the farmer chose. The history now holds a bare
    "Rampur", the list, the pick and the answer; the picked line is the place
    the farmer means, not the bare name asked again."""

    history = [
        *_asked_which_rampur(),
        ConversationMessage(role="user", text="Himachal"),
        ConversationMessage(
            role="assistant",
            text="Rampur, Himachal Pradesh: clear skies, 18°C today.",
        ),
    ]

    classification = await classify_intent(
        _turn("And tomorrow?", history=history), _live_llm()
    )

    assert len(classification.asks) == 1, f"expected one ask, got {classification!r}"
    assert classification.asks[0].place_name == "Rampur, Himachal Pradesh"


async def test_a_pick_on_a_partial_turn_asks_only_what_was_left() -> None:
    """Pune was answered in the same message that asked which Aurangabad.
    The pick finishes Aurangabad; asking Pune again would repeat the answer."""

    history = [
        ConversationMessage(
            role="user", text="What is the weather in Pune and Aurangabad?"
        ),
        ConversationMessage(
            role="assistant",
            text=(
                "Pune: clear skies, 31°C today.\n"
                "Which Aurangabad?\n"
                "1. Aurangabad, Maharashtra\n2. Aurangabad, Bihar"
            ),
        ),
    ]

    classification = await classify_intent(_turn("2", history=history), _live_llm())

    assert len(classification.asks) == 1, f"expected one ask, got {classification!r}"
    assert classification.asks[0].place_name == "Aurangabad, Bihar"


@pytest.mark.parametrize("reply", ["Kerala", "5"], ids=["not-listed", "no-such-line"])
async def test_a_reply_that_picks_nothing_listed_is_not_turned_into_a_pick(
    reply: str,
) -> None:
    """A guess would give the farmer the wrong Rampur without a word. Whatever
    the model returns, it must not be one of the lines we offered."""

    classification = await classify_intent(
        _turn(reply, history=_asked_which_rampur()), _live_llm()
    )

    for ask in classification.asks:
        assert ask.place_name not in _RAMPUR_LINES, (
            f"guessed {ask.place_name!r} from {reply!r}"
        )


def _question_we_send(name: str) -> str:
    """The question our code sends for `name`, built from the real place list."""

    ask = ClassifiedAsk(
        subject_categories=SubjectCategory.WEATHER,
        interaction_type=InteractionType.OBSERVE,
        place_name=name,
    )
    intent = resolve_places(
        IntentClassification(asks=(ask,)), _turn("q"), lookup=LOOKUP
    )
    answer = answer_for_unplaced_asks(intent.asks, load_clarification_text())
    assert answer is not None, f"{name} is not ambiguous in the place list"
    return answer.content[0].text


async def _place_for_reply(name: str, reply: str) -> ResolvedPlace:
    """Ask about `name`, reply with `reply`, and return the place the code uses."""

    history = [
        ConversationMessage(role="user", text=f"What is the weather in {name}?"),
        ConversationMessage(role="assistant", text=_question_we_send(name)),
    ]
    turn = _turn(reply, history=history)
    classification = await classify_intent(turn, _live_llm())
    intent = resolve_places(classification, turn, lookup=LOOKUP)

    assert len(intent.asks) == 1, f"expected one ask, got {classification!r}"
    place = intent.asks[0].place
    assert isinstance(place, ResolvedPlace), f"not one place: {place!r}"
    return place


async def test_pick_district_with_same_name_block() -> None:
    """Madhubani is a district and a block in Bihar. Line 1 is the district.
    It used to loop."""

    place = await _place_for_reply("Madhubani", "1")

    assert place.within == ("India", "Bihar")


async def test_pick_state_from_long_list() -> None:
    """Fatehpur has 7 matches, so we list states. Picking Uttar Pradesh gives
    its district without asking again. We chose this; the test pins it."""

    place = await _place_for_reply("Fatehpur", "Uttar Pradesh")

    assert place.within == ("India", "Uttar Pradesh")
