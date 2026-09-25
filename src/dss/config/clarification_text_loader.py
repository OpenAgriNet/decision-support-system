"""Loads the clarification text a farmer reads.

Mirrors ``identity_loader.py``: a configured-but-missing path raises rather
than silently falling back to a different configuration.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from dss.core.channel.models import ClarificationText

_DEFAULTS = Path(__file__).parent / "defaults" / "clarification_text.yaml"


def load_clarification_text(path: Path | None = None) -> ClarificationText:
    """Load the clarification text.

    - ``path`` unset → bundled defaults (I have no custom config).
    - ``path`` set but missing → ``FileNotFoundError`` (I have a config + it
      isn't there; do not boot on a different one).
    """

    source = _DEFAULTS if path is None else path
    if not source.exists():
        raise FileNotFoundError(
            f"clarification text config path is set to {source} but no file "
            "is there — refusing to boot on a different configuration"
        )

    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    return ClarificationText(**data)
