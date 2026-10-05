"""Loads the redaction rules file (ADR-0015).

Redaction is opt-in. ``DSS_REDACTION_ENABLED`` switches it on, and then
``DSS_REDACTION_CONFIG_PATH`` must name a rules file that loads. The file has two
parts:

- ``entities:`` — the policy: per entity, keep the value for a provider, or
  destroy it. Core applies it, whichever identifier found the span.
- ``identifiers:`` — which identifiers run, each with a ``type`` and its own
  settings. This loader only checks that every entry names a type; the adapter
  for that type checks the rest when it is built
  (``adapters/pii_identifier/factory.py``).

No path, no file, or a malformed file stops the boot and says which.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from dss.core.redaction.models import EntityName, RedactionPolicy, ValueHandling

# A ready rules file for India. Nothing reads it unless a deployment points
# DSS_REDACTION_CONFIG_PATH at it.
EXAMPLE_RULES = Path(__file__).parent / "examples" / "redaction-rules.yaml"


class RedactionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entities: dict[EntityName, ValueHandling] = Field(min_length=1)
    identifiers: list[dict[str, Any]] = Field(min_length=1)

    @field_validator("identifiers")
    @classmethod
    def _typed(cls, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for i, entry in enumerate(entries):
            if not isinstance(entry.get("type"), str):
                raise ValueError(f"identifiers[{i}] has no 'type'")
        return entries

    @property
    def policy(self) -> RedactionPolicy:
        return RedactionPolicy(entities=self.entities)


def load_redaction_config(
    *, enabled: bool, path: Path | None
) -> RedactionConfig | None:
    """The rules file, or ``None`` when redaction is off."""

    if not enabled:
        return None
    if path is None:
        raise ValueError(
            "DSS_REDACTION_ENABLED is true but DSS_REDACTION_CONFIG_PATH is not "
            "set — refusing to boot without redaction rules"
        )
    if not path.exists():
        raise FileNotFoundError(
            f"redaction config path is set to {path} but no file is there — "
            "refusing to boot without redaction rules"
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return RedactionConfig.model_validate(data)
