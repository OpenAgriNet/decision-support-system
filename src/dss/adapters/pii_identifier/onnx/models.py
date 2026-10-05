"""The ONNX identifier's settings — one ``type: onnx`` entry in the rules file."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from dss.core.redaction.models import EntityName


class OnnxSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["onnx"] = "onnx"
    # Holds model.onnx, tokenizer.json and labels.json — see
    # scripts/export_onnx_ner.py.
    dir: Path
    entity: EntityName = "person"
    # A name the model is less sure of than this is left alone.
    min_score: float = Field(0.8, ge=0.0, le=1.0)
