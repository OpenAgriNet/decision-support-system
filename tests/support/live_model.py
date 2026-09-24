"""The gate for tests that call a real model.

Such a test skips unless the key its model needs is set. Opening on any key
made a test bound to an `openai:` model run, and fail, when only the Azure
key was set.
"""

from __future__ import annotations

from collections.abc import Mapping


def missing_model_key(model: str, env: Mapping[str, str]) -> str | None:
    """The key `model` needs that `env` lacks, or None when it is set.

    `azure:` models go through the Azure SDK; every other model string goes
    to the OpenAI provider (`entrypoint/composition.py` `_resolve_model`).
    """

    needed = "AZURE_OPENAI_API_KEY" if model.startswith("azure:") else "OPENAI_API_KEY"
    return None if env.get(needed) else needed
