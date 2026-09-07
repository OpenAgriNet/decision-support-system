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

    # Where the adopter policy pack is mounted. Unset → use the bundled defaults;
    # set-but-missing → raise (see policy_loader), never boot on a different config.
    policy_config_path: Path | None = None

    # --- HTTP entrypoint (ADR-0006) ---------------------------------------
    # The concrete build that served the turn, echoed as `context.version`.
    dss_release: str = "v1.0.0"
    # A cap, not a rate limit — per-user limits belong to the caller. Zero
    # refuses every turn, which is how the saturated path is tested.
    max_concurrent_turns: int = Field(32, ge=0)
    # Checked after decompression: a small gzip payload can expand well past it.
    max_body_bytes: int = Field(1_000_000, ge=1)
    # Stands in for a real readiness probe until there is a dependency to probe.
    ready: bool = True
    # Wire StubLLM instead of a real provider, so the endpoint can be exercised
    # with no API key and no network (docs/RUNNING.md). Never true in a
    # deployment — the answers are canned.
    stub_llm: bool = False

    # --- evidence ---------------------------------------------------------
    # The external evidence API does not exist yet, so records are written here
    # as JSON Lines in the meantime.
    evidence_dir: Path = Path("var/evidence")
    # Declared so the destination is configuration rather than a code change.
    # NOT wired — `entrypoint.composition.build_runner` warns if it is set.
    evidence_url: str | None = None

    @property
    def telemetry_path(self) -> Path:
        return self.evidence_dir / "telemetry.jsonl"

    @property
    def turns_path(self) -> Path:
        return self.evidence_dir / "turns.jsonl"
