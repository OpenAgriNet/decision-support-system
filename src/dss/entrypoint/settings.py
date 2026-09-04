"""Runtime knobs. Every number here is a guess until there is load to measure.

Defaults can be overridden from the environment with a `DSS_` prefix, e.g.
`DSS_EVIDENCE_URL`, `DSS_EVIDENCE_DIR`. This is a deliberate stopgap —
`pydantic-settings` is the proper answer and is already a dependency on the
provider-discovery branch; adopting it here would collide with that work.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


def _env(name: str, default: str) -> str:
    return os.environ.get(f"DSS_{name}", default)


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # The concrete build that served the turn. Distinct from the envelope
    # version: the contract shape and the release move independently.
    dss_release: str = "v1.0.0"

    # A cap, not a rate limit — per-user limits belong to the caller. Zero means
    # every turn is refused, which is how the saturated path is tested.
    max_concurrent_turns: int = Field(default=32, ge=0)

    # Checked after decompression: a small gzip payload can expand well past it.
    max_body_bytes: int = Field(default=1_000_000, ge=1)

    # Stands in for a real readiness probe until there is a dependency to probe.
    ready: bool = True

    # Where evidence lands. The external evidence API does not exist yet, so
    # records are written here as JSON Lines in the meantime.
    evidence_dir: Path = Field(
        default_factory=lambda: Path(_env("EVIDENCE_DIR", "var/evidence"))
    )

    # The external evidence endpoint, once there is one. Declared so the
    # destination is configuration rather than a code change — NOT yet wired,
    # and `composition.build_runner` warns if it is set.
    evidence_url: str | None = Field(
        default_factory=lambda: os.environ.get("DSS_EVIDENCE_URL")
    )

    @property
    def telemetry_path(self) -> Path:
        return self.evidence_dir / "telemetry.jsonl"

    @property
    def turns_path(self) -> Path:
        return self.evidence_dir / "turns.jsonl"
