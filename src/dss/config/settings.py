"""Runtime settings (spec 0002, plus the 0004 moderation LLM binding).

One source of truth per knob, each with its range declared so an unusable value
fails at startup rather than silently disabling a feature.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from dss.core.intent.models import ActionType


class Settings(BaseSettings):
    # extra="ignore": a real deployment's .env also carries non-DSS vars the LLM
    # SDK reads directly (OPENAI_API_KEY) and the Langfuse OTEL_* keys. Without
    # this, the dotenv source treats them as unknown fields and Settings() raises.
    model_config = SettingsConfigDict(
        env_prefix="DSS_", env_file=".env", extra="ignore"
    )

    # --- moderation LLM binding (spec 0004: separate from composition) ---
    moderation_model: str = "openai:gpt-4o-mini"
    moderation_temperature: float = Field(0.0, ge=0.0, le=2.0)  # a judgment
    moderation_timeout_seconds: float = Field(5.0, gt=0.0)
    moderation_retries: int = Field(1, ge=0)

    # Where the adopter policy pack is mounted. Unset → use the bundled defaults;
    # set-but-missing → raise (see policy_loader), never boot on a different config.
    policy_config_path: Path | None = None

    # --- intent (spec 0002) — unused by this slice's two policies, kept for the
    # moderation context and the capability check that lands with intent. ---
    intent_confidence_min: float = Field(0.5, ge=0.0, le=1.0)
    supported_action_types: list[ActionType] = Field(
        default_factory=lambda: [ActionType.ADVISORY]
    )
