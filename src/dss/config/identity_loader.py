"""Loads the assistant's identity (design doc §6.7).

Mirrors ``policy_loader.py``: a configured-but-missing path raises rather
than silently falling back to a different configuration.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from dss.core.planner.models import Identity

# The identity that ships in the image. Adopters mount their own via
# DSS_IDENTITY_CONFIG_PATH; absent that, this is used.
_DEFAULTS = Path(__file__).parent / "defaults" / "identity.yaml"


def load_identity(path: Path | None = None) -> Identity:
    """Load the assistant's identity.

    - ``path`` unset → bundled defaults (I have no custom config).
    - ``path`` set but missing → ``FileNotFoundError`` (I have a config + it
      isn't there; do not boot on a different one).
    """

    source = _DEFAULTS if path is None else path
    if not source.exists():
        raise FileNotFoundError(
            f"identity config path is set to {source} but no file is there — "
            "refusing to boot on a different configuration"
        )

    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    return Identity(**data)
