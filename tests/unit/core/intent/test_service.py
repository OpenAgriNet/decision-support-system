"""Tier-1 unit tests for intent recognition.

Core logic only: the real LLM is replaced by a fake ``LLMProvider`` so these
exercise the *rules* ``classify`` owns — decomposition into asks, dropping
out-of-taxonomy and malformed asks, empty-asks-is-valid, confidence handling,
and timeout propagation — with plain Python in and out. No framework, no
network (``CLAUDE.md`` tier 1).
"""

from __future__ import annotations

import asyncio

import pytest

from dss.core.intent import BASE_TAXONOMY, ActionType, Intent, Taxonomy, classify
from dss.core.shared import TurnContext, UserTurn
from dss.ports import LLMError, LLMTimeoutError


class FakeLLM:
    """A stand-in ``LLMProvider`` that returns or raises whatever a test sets.

    Records each call so a test can assert what prompt/schema core built.
    """

    def __init__(self, *, returns=None, raises=None):
        self._returns = returns
        self._raises = raises
        self.calls: list[dict] = []

    async def generate_json(self, *, system, user, schema=None):
        self.calls.append({"system": system, "user": user, "schema": schema})
        if self._raises is not None:
            raise self._raises
        return self._returns


def _turn(query: str = "what is the price of potato?", source_lang: str = "en"):
    return UserTurn(
        query=query,
        source_lang=source_lang,
        target_lang=source_lang,
        channel="web",
        session_id="sess_1",
    )


def _classify(returns=None, *, raises=None, turn=None, taxonomy=BASE_TAXONOMY):
    llm = FakeLLM(returns=returns, raises=raises)
    ctx = TurnContext(llm=llm, trace_id="trc_1")
    intent = asyncio.run(classify(turn or _turn(), taxonomy, ctx))
    return intent, llm


def test_multi_ask_sentence_becomes_one_ask_each():
    # The spec's worked example: three questions in one sentence.
    returns = {
        "asks": [
            {"subject": "potato", "category": "Market", "action_type": "lookup"},
            {"subject": "PM-KISAN", "category": "Scheme", "action_type": "advisory"},
            {"subject": "potato", "category": "Crop", "action_type": "advisory"},
        ],
        "confidence": 0.9,
    }
    intent, _ = _classify(returns)

    assert isinstance(intent, Intent)
    assert intent.asks == (
        _ask("potato", "Market", ActionType.LOOKUP),
        _ask("PM-KISAN", "Scheme", ActionType.ADVISORY),
        _ask("potato", "Crop", ActionType.ADVISORY),
    )
    assert intent.confidence == 0.9


def test_category_not_in_taxonomy_is_dropped():
    returns = {
        "asks": [
            {"subject": "gold", "category": "Bullion", "action_type": "lookup"},
            {"subject": "potato", "category": "Market", "action_type": "lookup"},
        ],
        "confidence": 0.7,
    }
    intent, _ = _classify(returns)

    # Only the in-taxonomy ask survives; "Bullion" is not coerced to anything.
    assert intent.asks == (_ask("potato", "Market", ActionType.LOOKUP),)


def test_empty_asks_is_a_valid_intent_not_an_error():
    intent, _ = _classify({"asks": [], "confidence": 0.1})

    assert intent.asks == ()
    assert intent.confidence == 0.1


def test_missing_asks_key_yields_empty_asks():
    intent, _ = _classify({"confidence": 0.4})

    assert intent.asks == ()


def test_subject_is_none_when_the_question_names_no_subject():
    returns = {
        "asks": [{"subject": None, "category": "Weather", "action_type": "lookup"}],
        "confidence": 0.8,
    }
    intent, _ = _classify(returns)

    assert intent.asks == (_ask(None, "Weather", ActionType.LOOKUP),)


def test_blank_subject_is_normalised_to_none():
    returns = {
        "asks": [{"subject": "   ", "category": "Weather", "action_type": "lookup"}],
        "confidence": 0.8,
    }
    intent, _ = _classify(returns)

    assert intent.asks[0].subject is None


def test_subject_whitespace_is_trimmed():
    returns = {
        "asks": [
            {"subject": "  potato ", "category": "Crop", "action_type": "advisory"}
        ],
        "confidence": 0.8,
    }
    intent, _ = _classify(returns)

    assert intent.asks[0].subject == "potato"


def test_category_matching_is_case_insensitive():
    returns = {
        "asks": [{"subject": "potato", "category": "market", "action_type": "lookup"}],
        "confidence": 0.8,
    }
    intent, _ = _classify(returns)

    # Resolved to the taxonomy's canonical casing.
    assert intent.asks[0].category == "Market"


def test_malformed_action_type_drops_the_ask():
    returns = {
        "asks": [
            {"subject": "potato", "category": "Crop", "action_type": "purchase"},
            {"subject": "potato", "category": "Market", "action_type": "lookup"},
        ],
        "confidence": 0.6,
    }
    intent, _ = _classify(returns)

    # "purchase" is not a known action_type; that ask is dropped, not guessed.
    assert intent.asks == (_ask("potato", "Market", ActionType.LOOKUP),)


def test_action_type_matching_is_case_insensitive():
    returns = {
        "asks": [{"subject": "potato", "category": "Crop", "action_type": "ADVISORY"}],
        "confidence": 0.6,
    }
    intent, _ = _classify(returns)

    assert intent.asks[0].action_type == ActionType.ADVISORY


def test_non_object_ask_items_are_dropped():
    returns = {
        "asks": [
            "not an object",
            {"subject": "potato", "category": "Crop", "action_type": "advisory"},
        ],
        "confidence": 0.5,
    }
    intent, _ = _classify(returns)

    assert intent.asks == (_ask("potato", "Crop", ActionType.ADVISORY),)


def test_three_asks_do_not_force_low_confidence():
    returns = {
        "asks": [
            {"subject": "potato", "category": "Market", "action_type": "lookup"},
            {"subject": "PM-KISAN", "category": "Scheme", "action_type": "advisory"},
            {"subject": "potato", "category": "Crop", "action_type": "advisory"},
        ],
        "confidence": 0.95,
    }
    intent, _ = _classify(returns)

    assert intent.confidence == 0.95


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (1.5, 1.0),  # clamped down
        (-0.2, 0.0),  # clamped up
        ("high", 0.0),  # non-numeric → conservative default
        (None, 0.0),  # missing → conservative default
        (True, 0.0),  # bool is not a score
    ],
)
def test_confidence_is_clamped_and_defaults_low(raw, expected):
    intent, _ = _classify({"asks": [], "confidence": raw})

    assert intent.confidence == expected


def test_timeout_propagates_and_never_fabricates_an_intent():
    # A timeout must raise — classify must not invent an Intent (§7).
    with pytest.raises(LLMTimeoutError):
        _classify(raises=LLMTimeoutError("deadline"))


def test_generic_llm_error_propagates():
    with pytest.raises(LLMError):
        _classify(raises=LLMError("boom"))


def test_prompt_carries_the_taxonomy_and_query():
    taxonomy = Taxonomy(categories=("Crop", "Market"))
    _, llm = _classify(
        {"asks": [], "confidence": 0.0},
        turn=_turn(query="when should I sow potato?", source_lang="hi"),
        taxonomy=taxonomy,
    )

    call = llm.calls[0]
    assert "Crop" in call["system"] and "Market" in call["system"]
    assert "when should I sow potato?" in call["user"]
    assert "hi" in call["user"]
    # The schema constrains the model to this taxonomy's categories.
    category_enum = call["schema"]["properties"]["asks"]["items"]["properties"][
        "category"
    ]["enum"]
    assert category_enum == ["Crop", "Market"]


def _ask(subject, category, action_type):
    from dss.core.intent import Ask

    return Ask(subject=subject, category=category, action_type=action_type)
