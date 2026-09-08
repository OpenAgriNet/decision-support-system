"""Tier 1 — Settings binding and its tolerance of unrelated env vars."""

from __future__ import annotations

import pytest

from dss.config.settings import Settings


def test_defaults() -> None:
    settings = Settings()
    assert settings.moderation_temperature == 0.0
    assert settings.intent_model == "openai:gpt-4o-mini"
    assert settings.moderation_model == "openai:gpt-4o-mini"


def test_the_planner_and_composer_bind_their_own_models() -> None:
    """ADR-0004: each component binds its own model. The planner and composer
    were the two that did not — they took whatever a caller passed and set no
    temperature, timeout or retries at all, while intent and moderation read
    all three from here."""

    settings = Settings()

    assert settings.planner_model
    assert settings.planner_temperature == 0.0
    assert settings.planner_timeout_seconds == 30.0
    # the planner's design leans on ModelRetry in three places, so the
    # framework default of 1 is too few
    assert settings.planner_retries == 3

    assert settings.composer_model
    assert settings.composer_timeout_seconds == 30.0


def test_the_planner_model_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DSS_PLANNER_MODEL", "anthropic:claude-sonnet-5")
    monkeypatch.setenv("DSS_PLANNER_RETRIES", "5")

    settings = Settings()

    assert settings.planner_model == "anthropic:claude-sonnet-5"
    assert settings.planner_retries == 5


def test_provider_call_timeout_and_retries_have_defaults() -> None:
    """A slow provider must not block a turn indefinitely, and a transient
    failure gets more than one chance. Both configurable per deployment."""

    settings = Settings()

    assert settings.select_timeout_seconds == 5.0
    assert settings.select_attempts == 3
    assert settings.select_backoff_seconds == 0.5


def test_provider_call_retries_are_configurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DSS_SELECT_ATTEMPTS", "5")
    monkeypatch.setenv("DSS_SELECT_TIMEOUT_SECONDS", "2.5")

    settings = Settings()

    assert settings.select_attempts == 5
    assert settings.select_timeout_seconds == 2.5


def test_zero_attempts_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero attempts would never call the provider at all — a config that
    silently answers nothing."""

    monkeypatch.setenv("DSS_SELECT_ATTEMPTS", "0")

    with pytest.raises(ValueError):
        Settings()


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
