"""Loads and validates the redaction rules (#133).

Redaction is opt-in. ``DSS_REDACTION_ENABLED`` switches it on, and then
``DSS_REDACTION_CONFIG_PATH`` must name a rules file that loads. Anything else —
no path, no file, a pattern that does not compile — stops the boot and says which,
so a mistake is never discovered on a farmer's turn.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from dss.core.redaction.models import RedactionConfig

# A ready rules file for India. Nothing reads it unless a deployment points
# DSS_REDACTION_CONFIG_PATH at it.
EXAMPLE_RULES = Path(__file__).parent / "examples" / "redaction-rules.yaml"


def load_redaction_config(
    *, enabled: bool, path: Path | None
) -> RedactionConfig | None:
    """The rules to redact with, or ``None`` when redaction is off."""

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
