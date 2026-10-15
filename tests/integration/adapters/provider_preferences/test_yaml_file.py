"""Tier 2 — the preferences file, read off disk.

A ranking is exclusive, so a file that loads when it should not is worse than
one that refuses: a typo'd topic would drop every provider for that topic and
read to everyone as "nobody serves this". Every test here is about which
files are allowed to boot.
"""

from __future__ import annotations

from pathlib import Path

import anyio
import pytest

from dss.adapters.provider_preferences.yaml_file import (
    EXAMPLE_PREFERENCES,
    MalformedProviderPreferencesError,
    load_provider_preferences,
)


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "provider-preferences.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def _for(path: Path | None, client: str | None):
    return anyio.run(load_provider_preferences(path).for_client, client)


def test_an_unmounted_file_ranks_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """Unset is a real deployment state — every ask keeps what discovery
    found — but an operator has to be told, or a ranking that silently never
    applied looks like one that was never written."""

    with caplog.at_level("WARNING"):
        preference = _for(None, "kisan-mitra-web")

    assert preference.ranking_for("Weather") == ()
    assert "DSS_PROVIDER_PREFERENCES_PATH" in caplog.text


def test_a_configured_path_with_no_file_refuses_to_boot(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_provider_preferences(tmp_path / "absent.yaml")


def test_a_client_ranking_loads_in_the_order_it_was_written(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        version: 1
        clients:
          kisan-mitra-web:
            Weather: [maharashtra-vistaar, mausamgram]
        """,
    )

    preference = _for(path, "kisan-mitra-web")

    assert preference.ranking_for("Weather") == ("maharashtra-vistaar", "mausamgram")


def test_an_unknown_client_falls_back_to_the_default(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        version: 1
        default:
          Weather: [mausamgram]
        clients:
          kisan-mitra-web:
            Weather: [maharashtra-vistaar]
        """,
    )

    assert _for(path, "someone-else").ranking_for("Weather") == ("mausamgram",)
    assert _for(path, None).ranking_for("Weather") == ("mausamgram",)


def test_a_topic_outside_the_taxonomy_refuses_to_boot(tmp_path: Path) -> None:
    """ "Wether" would load cleanly and then never match an ask."""

    path = _write(
        tmp_path,
        """
        version: 1
        default:
          Wether: [mausamgram]
        """,
    )

    with pytest.raises(MalformedProviderPreferencesError, match="Wether"):
        load_provider_preferences(path)


def test_an_empty_ranking_refuses_to_boot(tmp_path: Path) -> None:
    """Exclusive plus empty means the topic has no provider at all. Nobody
    asks for that by leaving brackets empty."""

    path = _write(
        tmp_path,
        """
        version: 1
        default:
          Weather: []
        """,
    )

    with pytest.raises(
        MalformedProviderPreferencesError, match="at least one provider"
    ):
        load_provider_preferences(path)


def test_a_provider_listed_twice_refuses_to_boot(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        version: 1
        default:
          Weather: [mausamgram, mausamgram]
        """,
    )

    with pytest.raises(MalformedProviderPreferencesError, match="twice"):
        load_provider_preferences(path)


def test_a_blank_client_id_refuses_to_boot(tmp_path: Path) -> None:
    """It could never match a header value."""

    path = _write(
        tmp_path,
        """
        version: 1
        clients:
          "  ":
            Weather: [mausamgram]
        """,
    )

    with pytest.raises(MalformedProviderPreferencesError, match="blank id"):
        load_provider_preferences(path)


def test_an_unknown_file_version_refuses_to_boot(tmp_path: Path) -> None:
    path = _write(tmp_path, "version: 2\ndefault: {}\n")

    with pytest.raises(MalformedProviderPreferencesError, match="version 2"):
        load_provider_preferences(path)


def test_an_unknown_top_level_key_refuses_to_boot(tmp_path: Path) -> None:
    """A misspelled `clients:` would otherwise load as an empty book."""

    path = _write(tmp_path, "version: 1\nclientz: {}\n")

    with pytest.raises(MalformedProviderPreferencesError):
        load_provider_preferences(path)


def test_broken_yaml_refuses_to_boot(tmp_path: Path) -> None:
    path = _write(tmp_path, "version: 1\ndefault: [unclosed\n")

    with pytest.raises(MalformedProviderPreferencesError):
        load_provider_preferences(path)


def test_the_shipped_example_file_loads() -> None:
    """It is there to be copied, so it has to be a file that boots."""

    preference = _for(EXAMPLE_PREFERENCES, "kisan-mitra-web")

    assert preference.known is True
    assert preference.ranking_for("Weather")[0] == "maharashtra-vistaar"
    assert preference.ranking_for("Market") == ("agmarknet-mock",)
