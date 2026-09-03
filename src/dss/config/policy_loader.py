"""Loads and validates the policy pack (spec 0003, focused slice).

Validation is Pydantic only: ``yaml.safe_load`` → ``PolicyPack(**data)``, with two
corrections to the sibling repos' precedent, both cases where a config mistake
currently produces wrong behaviour instead of an error:

- ``extra="forbid"`` on the models: a misspelled field fails at boot, named.
- A configured path that isn't there raises, rather than silently falling back to
  a different configuration.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from dss.core.policy.models import PolicyPack

# The pack that ships in the image. Adopters mount their own via
# DSS_POLICY_CONFIG_PATH; absent that, this is used.
_DEFAULTS = Path(__file__).parent / "defaults" / "policies.yaml"


def load_policy_pack(path: Path | None = None) -> PolicyPack:
    """Load the policy pack.

    - ``path`` unset → bundled defaults (I have no custom config).
    - ``path`` set but missing → ``FileNotFoundError`` (I have a config + it isn't
      there; do not boot on a different one).
    """

    if path is None:
        source = _DEFAULTS
    else:
        source = path
        if not source.exists():
            raise FileNotFoundError(
                f"policy config path is set to {source} but no file is there — "
                "refusing to boot on a different configuration"
            )

    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    return PolicyPack(**data)
