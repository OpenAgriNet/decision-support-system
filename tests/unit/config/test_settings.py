"""Tier 1 — Settings binding and its tolerance of unrelated env vars."""

from __future__ import annotations

import pytest

from dss.config.settings import Settings
from dss.core.intent.models import ActionType


def test_defaults() -> None:
    settings = Settings()
    assert settings.moderation_temperature == 0.0
    assert settings.intent_confidence_min == 0.5
    assert settings.supported_action_types == [ActionType.ADVISORY]


def test_dss_prefixed_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DSS_MODERATION_MODEL", "openai:gpt-4o")
    assert Settings().moderation_model == "openai:gpt-4o"


def test_unrelated_env_vars_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    # An LLM/Langfuse key in the environment must not make Settings() raise.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:3000")
    Settings()  # must not raise


def test_out_of_range_temperature_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DSS_MODERATION_TEMPERATURE", "9")
    with pytest.raises(ValueError):
        Settings()
