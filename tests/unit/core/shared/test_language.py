"""The language tag: what any BCP-47 tag means, with no DSS policy on it."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from dss.core.shared.language import ENGLISH, LanguageTag


class _Tagged(BaseModel):
    lang: LanguageTag


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("GU-in", "gu-IN"),
        ("zh-hant-tw", "zh-Hant-TW"),
        ("es-419", "es-419"),
        ("SL-Rozaj-BISKE", "sl-rozaj-biske"),
    ],
)
def test_a_tag_is_stored_in_standard_casing(raw: str, expected: str) -> None:
    assert LanguageTag(raw).root == expected


@pytest.mark.parametrize("raw", ["gujarati", "en-a-b-c", "", "hi_IN", "x-private"])
def test_a_malformed_tag_is_rejected(raw: str) -> None:
    with pytest.raises(ValueError, match="is not a supported BCP-47 tag"):
        LanguageTag(raw)


def test_a_tag_built_from_a_tag_is_the_same_tag() -> None:
    tag = LanguageTag("HI-in")
    assert LanguageTag(tag) == tag  # type: ignore[arg-type]  # the misuse under test


def test_str_is_the_bare_tag() -> None:
    tag = LanguageTag("hi-IN")
    assert str(tag) == "hi-IN"
    assert f"{tag}" == "hi-IN"


def test_primary_is_the_language_subtag() -> None:
    assert LanguageTag("gu-Gujr-IN").primary == "gu"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("gu", ("gu",)),
        ("hi-IN", ("hi-IN", "hi")),
        ("gu-Gujr-IN", ("gu-Gujr-IN", "gu-Gujr", "gu")),
    ],
)
def test_lookup_order_drops_subtags_from_the_end(
    raw: str, expected: tuple[str, ...]
) -> None:
    assert LanguageTag(raw).lookup_order == tuple(LanguageTag(t) for t in expected)


def test_a_regional_tag_is_within_its_language() -> None:
    assert LanguageTag("en-IN").is_within(ENGLISH)
    assert ENGLISH.is_within(ENGLISH)


def test_a_sibling_region_is_not_within() -> None:
    assert not LanguageTag("en-US").is_within(LanguageTag("en-GB"))
    assert not LanguageTag("hi").is_within(ENGLISH)


def test_equality_and_hash_use_the_standard_form() -> None:
    assert LanguageTag("HI-in") == LanguageTag("hi-IN")
    assert len({LanguageTag("HI-in"), LanguageTag("hi-IN")}) == 1


def test_a_tag_never_equals_a_plain_string() -> None:
    assert LanguageTag("hi") != "hi"


def test_a_model_field_accepts_a_string_and_dumps_one() -> None:
    tagged = _Tagged.model_validate({"lang": "HI-in"})

    assert tagged.lang == LanguageTag("hi-IN")
    assert tagged.model_dump(mode="json") == {"lang": "hi-IN"}
    assert _Tagged.model_validate_json(tagged.model_dump_json()) == tagged
