"""Runtime settings (spec 0002, plus the 0004 moderation LLM binding).

One source of truth per knob, each with its range declared so an unusable value
fails at startup rather than silently disabling a feature.

Each component binds its *own* model (ADR-0004): intent and moderation are
separate ``DSS_<COMPONENT>_MODEL`` knobs so a deployment can point them at
different models. The model name always comes from the environment
(``env_prefix="DSS_"``): ``DSS_INTENT_MODEL``, ``DSS_MODERATION_MODEL``, etc.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # extra="ignore": a real deployment's .env also carries non-DSS vars the LLM
    # SDK reads directly (OPENAI_API_KEY) and the Langfuse OTEL_* keys. Without
    # this, the dotenv source treats them as unknown fields and Settings() raises.
    model_config = SettingsConfigDict(
        env_prefix="DSS_", env_file=".env", extra="ignore"
    )

    # --- intent LLM binding (spec 0002) ---
    intent_model: str = "openai:gpt-4o-mini"
    intent_temperature: float = Field(0.0, ge=0.0, le=2.0)  # a classification
    intent_timeout_seconds: float = Field(5.0, gt=0.0)
    intent_retries: int = Field(1, ge=0)

    # --- moderation LLM binding (spec 0004: separate from composition) ---
    moderation_model: str = "openai:gpt-4o-mini"
    moderation_temperature: float = Field(0.0, ge=0.0, le=2.0)  # a judgment
    moderation_timeout_seconds: float = Field(5.0, gt=0.0)
    moderation_retries: int = Field(1, ge=0)

    # --- provider invocation (/select) ---
    # One slow provider must not block the turn. The timeout bounds a single
    # call; attempts bound how many times a *transient* failure is retried (a
    # defect is never retried — the same malformed request fails the same
    # way). Backoff doubles per attempt, because 429 is transient too and
    # retrying at once is what caused it.
    select_timeout_seconds: float = Field(5.0, gt=0.0)
    select_attempts: int = Field(3, ge=1)  # 0 would never call the provider
    select_backoff_seconds: float = Field(0.5, ge=0.0)

    # Where the adopter policy pack is mounted. Unset → use the bundled defaults;
    # set-but-missing → raise (see policy_loader), never boot on a different config.
    policy_config_path: Path | None = None
