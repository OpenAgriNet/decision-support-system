"""Tier 1 — Settings binding and its tolerance of unrelated env vars."""

from __future__ import annotations

import pytest

from dss.config.settings import Settings


def test_defaults() -> None:
    settings = Settings()
    assert settings.moderation_temperature == 0.0
    assert settings.intent_model == "openai:gpt-4o-mini"
    assert settings.moderation_model == "openai:gpt-4o-mini"


def test_each_component_binds_its_own_model(monkeypatch: pytest.MonkeyPatch) -> None:
    # The model name comes from the environment, per component (ADR-0004).
    monkeypatch.setenv("DSS_INTENT_MODEL", "openai:gpt-4o")
    monkeypatch.setenv("DSS_MODERATION_MODEL", "anthropic:claude-sonnet-5")
    settings = Settings()
    assert settings.intent_model == "openai:gpt-4o"
    assert settings.moderation_model == "anthropic:claude-sonnet-5"


def test_unrelated_env_vars_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    # An LLM/Langfuse key in the environment must not make Settings() raise.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:3000")
    Settings()  # must not raise


def test_out_of_range_temperature_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DSS_MODERATION_TEMPERATURE", "9")
    with pytest.raises(ValueError):
        Settings()
