from __future__ import annotations

import pytest
from pydantic import ValidationError

from dss.config.clarification_text_loader import load_clarification_text
from dss.core.channel.models import ClarificationText


def test_a_field_message_with_an_unknown_placeholder_is_refused_at_load() -> None:
    """An adopter's typo must stop the boot, not the first farmer's submission."""

    text = load_clarification_text().model_dump()
    text["field_pattern"] = "{label} must look like {example}"

    with pytest.raises(ValidationError, match="field_pattern"):
        ClarificationText(**text)
