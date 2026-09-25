"""Tier 1 — when a live-model test may run.

A live-model test must skip without the key its model needs, never fail. The
gate used to open on any key, so with only the Azure key set, a test bound to
an `openai:` model ran and failed on the missing OpenAI key.
"""

from __future__ import annotations

from tests.support.live_model import missing_model_key


def test_an_openai_model_needs_the_openai_key_not_just_any_key():
    env = {"AZURE_OPENAI_API_KEY": "k"}

    assert missing_model_key("openai:gemma-4-31b-it", env) == "OPENAI_API_KEY"
