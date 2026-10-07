"""The composition root's own behaviour: where evidence goes, and what it says
when it is configured to go somewhere it cannot."""

from __future__ import annotations

import warnings

import httpx
import pytest

from dss.adapters.area_lookup.cache import CachedAreaLookup
from dss.config.settings import Settings
from dss.entrypoint.composition import build_area_lookup, build_runner


def _photon_settings(url: str | None = "http://photon.test") -> Settings:
    """Settings that ignore a developer's local `.env`. Tests that mean to set
    something use `monkeypatch.setenv`, which this still reads."""

    return Settings(photon_base_url=url, _env_file=None)


def test_evidence_paths_sit_under_the_configured_directory(tmp_path):
    settings = Settings(evidence_dir=tmp_path / "evidence")

    assert settings.telemetry_path == tmp_path / "evidence" / "telemetry.jsonl"
    assert settings.turns_path == tmp_path / "evidence" / "turns.jsonl"


def test_a_configured_url_is_not_silently_ignored(tmp_path):
    """A setting that does nothing is worse than a missing one — it reads as
    working. Warn until the endpoint is real."""

    settings = Settings(
        evidence_dir=tmp_path, evidence_url="https://evidence.internal/v1"
    )

    with pytest.warns(UserWarning, match="not implemented"):
        build_runner(settings)


def test_without_a_photon_url_only_the_csv_is_used():
    """Photon is off until its URL is set, so a deployment that never heard of
    it behaves as before."""

    lookup, _aclose = build_area_lookup(_photon_settings(url=None))

    assert [name for name, _ in lookup.sources] == ["csv"]


def test_an_empty_photon_url_keeps_photon_off():
    """`DSS_PHOTON_BASE_URL=` with nothing after it means "not set". Treating
    it as an address would make every lookup fail."""

    lookup, _aclose = build_area_lookup(_photon_settings(url=""))

    assert [name for name, _ in lookup.sources] == ["csv"]


def test_a_photon_url_adds_photon_after_the_csv():
    """The file answers first. Photon is asked only for names it lacks."""

    lookup, _aclose = build_area_lookup(_photon_settings())

    assert [name for name, _ in lookup.sources] == ["csv", "photon"]


def test_photon_covers_the_countries_the_file_covers():
    """No country setting: the shipped file is India, so Photon is kept to
    India. A same-name village in another country must not answer."""

    lookup, _aclose = build_area_lookup(_photon_settings())

    _name, photon = lookup.sources[1]
    assert photon.country_codes == ("IN",)


def test_the_country_setting_beats_the_file(monkeypatch):
    """An operator who names countries means them, whatever the file covers."""

    monkeypatch.setenv("DSS_PHOTON_COUNTRY_CODES", "KE,UG")

    lookup, _aclose = build_area_lookup(_photon_settings())

    _name, photon = lookup.sources[1]
    assert photon.country_codes == ("KE", "UG")


def test_nothing_is_kept_unless_the_cache_is_turned_on(monkeypatch):
    """Some geocoders forbid storing their answers, so keeping them is opt-in."""

    monkeypatch.delenv("DSS_PHOTON_CACHE_ENABLED", raising=False)

    lookup, _aclose = build_area_lookup(_photon_settings())

    _name, photon = lookup.sources[1]
    assert not isinstance(photon, CachedAreaLookup)


def test_the_cache_is_used_when_it_is_turned_on(monkeypatch):
    monkeypatch.setenv("DSS_PHOTON_CACHE_ENABLED", "true")

    lookup, _aclose = build_area_lookup(_photon_settings())

    _name, photon = lookup.sources[1]
    assert isinstance(photon, CachedAreaLookup)


async def test_closing_releases_the_photon_client(monkeypatch):
    """Photon holds its own connections. They must close on shutdown, not leak."""

    created: list[httpx.AsyncClient] = []

    class _Recording(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr(httpx, "AsyncClient", _Recording)
    _lookup, aclose = build_area_lookup(_photon_settings())

    await aclose()

    assert [client.is_closed for client in created] == [True]


def test_no_warning_when_no_url_is_configured(tmp_path):
    settings = Settings(evidence_dir=tmp_path)

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        build_runner(settings)
