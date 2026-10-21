"""Tier 1 — how a client's ranking resolves against the default underneath."""

from __future__ import annotations

from dss.core.provider_preferences.models import PreferenceBook

BOOK = PreferenceBook(
    default={"weather": ("imd", "mausamgram"), "market": ("agmarknet",)},
    senders={
        "kisan-mitra-web": {"weather": ("maharashtra-vistaar", "imd")},
        "vistaar-whatsapp": {"market": ("state-mandi",)},
    },
)


def test_a_client_gets_its_own_ranking_for_a_topic_it_ranked() -> None:
    assert BOOK.for_sender("kisan-mitra-web").ranking_for("Weather") == (
        "maharashtra-vistaar",
        "imd",
    )


def test_a_client_falls_back_to_the_default_per_topic_not_per_client() -> None:
    """Ranking weather must not cost a client the default's market list."""

    preference = BOOK.for_sender("kisan-mitra-web")

    assert preference.ranking_for("Market") == ("agmarknet",)
    assert preference.source_for("Market") == "default"
    assert preference.source_for("Weather") == "sender"


def test_a_clients_ranking_replaces_the_default_rather_than_extending_it() -> None:
    """The list is the whole answer for its topic. Appending the default
    underneath would mean a client could never rule a provider out, which is
    the point of vetting one."""

    assert BOOK.for_sender("vistaar-whatsapp").ranking_for("Market") == ("state-mandi",)


def test_an_unknown_client_is_served_the_default() -> None:
    preference = BOOK.for_sender("never-configured")

    assert preference.ranking_for("Weather") == ("imd", "mausamgram")
    assert preference.known is False


def test_a_turn_with_no_sender_id_is_served_the_default() -> None:
    preference = BOOK.for_sender(None)

    assert preference.ranking_for("Weather") == ("imd", "mausamgram")
    assert preference.known is False


def test_a_configured_client_is_known_even_when_it_matches_the_default() -> None:
    """`known` separates "asked for exactly the default" from "never heard of
    them" — identical in `by_topic`, different to an operator."""

    book = PreferenceBook(default={"weather": ("imd",)}, senders={"same": {}})

    assert book.for_sender("same").known is True
    assert book.for_sender("other").known is False


def test_a_topic_nobody_ranked_has_no_ranking() -> None:
    assert BOOK.for_sender("kisan-mitra-web").ranking_for("Livestock") == ()


def test_topic_names_match_whatever_case_the_file_used() -> None:
    book = PreferenceBook(default={"weather": ("imd",)}, senders={})

    assert book.for_sender(None).ranking_for("Weather") == ("imd",)
