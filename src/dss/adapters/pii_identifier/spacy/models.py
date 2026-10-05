"""The spaCy identifier's settings — one ``type: spacy`` entry in the rules file."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from dss.core.redaction.models import EntityName


class SpacySettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["spacy"] = "spacy"
    # The entity its names are reported as; the entities policy keeps or destroys it.
    entity: EntityName = "person"
    model: str = "en_core_web_sm"
