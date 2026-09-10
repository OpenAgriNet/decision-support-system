"""Tier 1 — alias and query normalization. Plain text in, tokens out."""

from __future__ import annotations

from dss.core.enrichment.normalize import alias_key, normalize_tokens


def test_casefolds_and_splits_on_whitespace() -> None:
    assert normalize_tokens("Micro Irrigation Fund") == ("micro", "irrigation", "fund")


def test_hyphen_and_en_dash_become_separators() -> None:
    """`PM-DDKY` and `pm ddky` are the same alias, and the source document
    spells one scheme with an en dash (`Dhan–Dhaanya`)."""

    assert alias_key("PM-DDKY") == alias_key("pm ddky") == "pm ddky"
    assert alias_key("Dhan–Dhaanya") == "dhan dhaanya"


def test_punctuation_and_repeated_spaces_collapse() -> None:
    assert alias_key("  e-NAM?  ") == "e nam"


def test_non_latin_script_survives() -> None:
    """The DSS is multilingual, so normalization must not delete a Devanagari
    query outright — a tenant may add Devanagari aliases to its catalog."""

    assert normalize_tokens("मखाना योजना") == ("मखाना", "योजना")


def test_empty_text_yields_no_tokens() -> None:
    assert normalize_tokens("   ") == ()
    assert alias_key("!!") == ""
