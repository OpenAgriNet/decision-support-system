"""Tier 1 — swapping tags back for the provider, and hiding values it echoes."""

from __future__ import annotations

from dss.core.redaction.reveal import RevealMap, StreamReveal

MAP = RevealMap(
    values={"«phone_1»": "9876543210", "«person_1»": "Ramesh"},
)


def test_reveal_swaps_known_tags_anywhere_in_the_body() -> None:
    body = {
        "applicant": {"phone": "«phone_1»", "name": "«person_1»"},
        "notes": ["call «phone_1» after 5"],
        "count": 2,
    }
    assert MAP.reveal(body) == {
        "applicant": {"phone": "9876543210", "name": "Ramesh"},
        "notes": ["call 9876543210 after 5"],
        "count": 2,
    }


def test_reveal_leaves_a_tag_it_does_not_hold() -> None:
    # A destroyed entity has no entry, so its tag goes out as the tag.
    assert MAP.reveal({"id": "«aadhaar_1»"}) == {"id": "«aadhaar_1»"}


def test_reveal_does_not_change_the_input() -> None:
    body = {"phone": "«phone_1»"}
    MAP.reveal(body)
    assert body == {"phone": "«phone_1»"}


def test_conceal_hides_an_echoed_value() -> None:
    assert (
        MAP.conceal("status for 9876543210: approved")
        == "status for «phone_1»: approved"
    )


def test_conceal_hides_an_echo_written_with_gaps() -> None:
    assert MAP.conceal("mobile 98765 43210 registered") == "mobile «phone_1» registered"


def test_conceal_leaves_other_numbers_alone() -> None:
    # A KVK officer's number is information the farmer asked for.
    assert MAP.conceal("call KVK at 9123456789") == "call KVK at 9123456789"


def test_conceal_matches_whole_words_only() -> None:
    assert MAP.conceal("Rameshwar mandi") == "Rameshwar mandi"
    assert MAP.conceal("ramesh ji") == "«person_1» ji"


def test_an_empty_map_changes_nothing() -> None:
    empty = RevealMap(values={})
    assert empty.reveal({"a": "«phone_1»"}) == {"a": "«phone_1»"}
    assert empty.conceal("9876543210") == "9876543210"


def test_conceal_hides_an_echo_without_the_country_code() -> None:
    # The farmer typed "+91 98765 43210"; the provider replies with the bare number.
    held = RevealMap(values={"«phone_1»": "+919876543210"})
    assert held.conceal("status for 9876543210: approved") == (
        "status for «phone_1»: approved"
    )
    assert held.conceal("status for +91 98765 43210") == "status for «phone_1»"


def test_conceal_with_a_country_code_leaves_other_numbers_alone() -> None:
    held = RevealMap(values={"«phone_1»": "+919876543210"})
    assert held.conceal("call KVK at 9123456789") == "call KVK at 9123456789"


# --- the answer streamed to the farmer --------------------------------------


def _stream(pieces: list[str]) -> list[str]:
    stream = StreamReveal(MAP)
    out = [stream.feed(piece) for piece in pieces]
    out.append(stream.flush())
    return out


def test_stream_reveals_a_whole_tag_in_one_piece() -> None:
    assert "".join(_stream(["we will call «phone_1» today"])) == (
        "we will call 9876543210 today"
    )


def test_stream_holds_back_a_tag_split_across_pieces() -> None:
    out = _stream(["we will call «pho", "ne_1» today"])

    # Nothing half-tagged is shown; the tag comes out whole, revealed.
    assert "«" not in out[0]
    assert "".join(out) == "we will call 9876543210 today"


def test_stream_leaves_a_destroyed_tag_as_the_tag() -> None:
    assert "".join(_stream(["your «aadhaar_1» was not kept"])) == (
        "your «aadhaar_1» was not kept"
    )


def test_stream_flushes_an_open_bracket_that_never_closed() -> None:
    # A stray « is text, not a tag. It must not be swallowed.
    assert "".join(_stream(["price « 2,275"])) == "price « 2,275"


def test_stream_with_an_empty_map_passes_pieces_straight_through() -> None:
    stream = StreamReveal(RevealMap(values={}))
    assert stream.feed("call «phone_1»") == "call «phone_1»"
    assert stream.flush() == ""


def test_conceal_body_hides_echoes_in_strings_and_bare_numbers() -> None:
    body = {
        "status": "approved for 98765 43210",
        "mobile": 9876543210,
        "count": 2,
        "items": [{"note": "Ramesh ji"}],
    }
    assert MAP.conceal_body(body) == {
        "status": "approved for «phone_1»",
        "mobile": "«phone_1»",
        "count": 2,
        "items": [{"note": "«person_1» ji"}],
    }
