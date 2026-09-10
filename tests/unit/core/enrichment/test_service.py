"""Tier 1 — the scheme resolver. Plain domain objects in and out; the catalog
is a plain mapping, so no port double is needed.
"""

from __future__ import annotations

from dss.core.enrichment.models import Scheme
from dss.core.enrichment.normalize import alias_key
from dss.core.enrichment.service import find_scheme, resolve_scheme_subjects
from dss.core.intent.models import Ask, Intent, InteractionType, SubjectCategory

MAKHANA = Scheme(
    code="makhana",
    name="Central Sector Scheme for Development of Makhana",
)
PKVY = Scheme(code="pkvy", name="Paramparagat Krishi Vikas Yojana")

# What the CSV adapter builds at boot: normalized alias -> scheme. Note
# `makhana` and `foxnut` are absent by design — a bare commodity word is a
# wrong alias, so "makhana price" can never resolve to a scheme.
ALIASES = {
    alias_key(text): scheme
    for scheme, texts in (
        (MAKHANA, ("makhana scheme", "development of makhana", MAKHANA.name)),
        (PKVY, ("PKVY", "paramparagat krishi vikas yojana", PKVY.name)),
    )
    for text in texts
}


def _ask(
    subject: str | None,
    category: SubjectCategory = SubjectCategory.SCHEME,
    interaction: InteractionType = InteractionType.ADVISE,
) -> Ask:
    return Ask(
        agriculture_subjects=subject,
        subject_categories=category,
        interaction_type=interaction,
    )


# --- find_scheme: the token-span scan ---------------------------------------


def test_finds_an_alias_inside_a_sentence() -> None:
    match = find_scheme("i want to know about makhana scheme", ALIASES)

    assert match is not None
    assert match.scheme == MAKHANA
    assert match.matched_alias == "makhana scheme"


def test_longest_span_wins() -> None:
    """`development of makhana` and the full scheme name are both aliases; the
    longer span is the more specific match."""

    match = find_scheme("tell me about the development of makhana", ALIASES)

    assert match is not None
    assert match.matched_alias == "development of makhana"


def test_matching_is_token_bounded_not_substring() -> None:
    """A naive `in` check finds `mif` inside `amplifier`."""

    mif = {alias_key("MIF"): Scheme(code="mif", name="Micro Irrigation Fund")}

    assert find_scheme("my amplifier is broken", mif) is None
    assert find_scheme("what is MIF?", mif) is not None


def test_an_acronym_alias_matches_case_insensitively() -> None:
    assert find_scheme("how do i apply for PKVY", ALIASES) is not None
    assert find_scheme("how do i apply for pkvy", ALIASES) is not None


def test_no_alias_in_the_text_is_no_match() -> None:
    assert find_scheme("what is the wheat price today", ALIASES) is None


def test_an_empty_catalog_matches_nothing() -> None:
    assert find_scheme("makhana scheme", {}) is None


# --- resolve_scheme_subjects: the category gate and the rewrite -------------


def test_canonicalizes_the_subject_of_a_scheme_ask() -> None:
    intent = Intent(asks=(_ask("makhana"),), confidence=0.9)

    resolution = resolve_scheme_subjects(
        intent, "i want to know about makhana scheme", ALIASES
    )

    assert resolution.intent.asks[0].agriculture_subjects == MAKHANA.name
    assert resolution.matches[0].scheme == MAKHANA


def test_leaves_a_non_scheme_ask_alone() -> None:
    """The category gate. `makhana scheme` is in the query, but the classifier
    called this a market lookup, so the price ask must not be rewritten."""

    intent = Intent(
        asks=(_ask("makhana", SubjectCategory.MARKET, InteractionType.OBSERVE),),
        confidence=0.9,
    )

    resolution = resolve_scheme_subjects(
        intent, "makhana scheme price in patna mandi", ALIASES
    )

    assert resolution.intent == intent
    assert resolution.matches == ()


def test_resolves_from_the_extracted_subject_alone() -> None:
    """The classifier may name a scheme the raw query never spelled out."""

    intent = Intent(asks=(_ask("Paramparagat Krishi Vikas Yojana"),), confidence=0.9)

    resolution = resolve_scheme_subjects(intent, "how do i go organic", ALIASES)

    assert resolution.intent.asks[0].agriculture_subjects == PKVY.name
    assert resolution.matches[0].matched_alias == alias_key(PKVY.name)


def test_falls_back_to_the_raw_query_when_the_subject_is_unlisted() -> None:
    """`makhana` is deliberately not an alias, so the subject alone resolves
    to nothing and the query has to carry it."""

    intent = Intent(asks=(_ask("makhana"),))

    resolution = resolve_scheme_subjects(intent, "about the makhana scheme", ALIASES)

    assert resolution.matches[0].matched_alias == "makhana scheme"


def test_two_scheme_asks_in_one_turn_resolve_independently() -> None:
    """The subject is tried before the query precisely for this: the query is
    shared by every ask, so searching it first would give both asks whichever
    match happened to be longest."""

    intent = Intent(asks=(_ask("PKVY"), _ask("makhana scheme")))

    resolution = resolve_scheme_subjects(
        intent, "tell me about pkvy and the makhana scheme", ALIASES
    )

    subjects = [ask.agriculture_subjects for ask in resolution.intent.asks]
    assert subjects == [PKVY.name, MAKHANA.name]


def test_preserves_category_and_interaction_type() -> None:
    intent = Intent(asks=(_ask("makhana", interaction=InteractionType.ACT),))

    ask = resolve_scheme_subjects(
        intent, "apply for makhana scheme", ALIASES
    ).intent.asks[0]

    assert ask.subject_categories is SubjectCategory.SCHEME
    assert ask.interaction_type is InteractionType.ACT


def test_rewrites_only_the_asks_that_match() -> None:
    intent = Intent(
        asks=(
            _ask("wheat", SubjectCategory.MARKET, InteractionType.OBSERVE),
            _ask("makhana"),
        ),
        confidence=0.8,
    )

    resolution = resolve_scheme_subjects(
        intent, "wheat price and the makhana scheme", ALIASES
    )

    assert resolution.intent.asks[0] == intent.asks[0]
    assert resolution.intent.asks[1].agriculture_subjects == MAKHANA.name
    assert len(resolution.matches) == 1
    assert resolution.intent.confidence == intent.confidence


def test_an_unresolvable_scheme_ask_is_left_as_the_farmer_said_it() -> None:
    """No catalog entry is not an error: the ask still reaches discovery, it
    just carries the farmer's own words."""

    intent = Intent(asks=(_ask("some scheme my neighbour got"),))

    resolution = resolve_scheme_subjects(
        intent, "some scheme my neighbour got", ALIASES
    )

    assert resolution.intent == intent
    assert resolution.matches == ()


def test_does_not_synthesise_an_ask_on_an_empty_intent() -> None:
    """Enrichment corrects the classifier; it never authors intent."""

    resolution = resolve_scheme_subjects(Intent(), "pkvy", ALIASES)

    assert resolution.intent.asks == ()
    assert resolution.matches == ()
