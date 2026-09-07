"""The composition root's own behaviour: where evidence goes, and what it says
when it is configured to go somewhere it cannot."""

from __future__ import annotations

import warnings

import pytest

from dss.entrypoint.composition import build_runner
from dss.entrypoint.settings import Settings


def test_evidence_paths_sit_under_the_configured_directory(tmp_path):
    settings = Settings(evidence_dir=tmp_path / "evidence")

    assert settings.telemetry_path == tmp_path / "evidence" / "telemetry.jsonl"
    assert settings.turns_path == tmp_path / "evidence" / "turns.jsonl"


def test_a_configured_url_is_not_silently_ignored(tmp_path):
    """A setting that does nothing is worse than a missing one — it reads as
    working. Warn until the endpoint is real."""

    settings = Settings(evidence_dir=tmp_path, evidence_url="https://evidence.internal/v1")

    with pytest.warns(UserWarning, match="not implemented"):
        build_runner(settings)


def test_no_warning_when_no_url_is_configured(tmp_path):
    settings = Settings(evidence_dir=tmp_path)

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        build_runner(settings)
