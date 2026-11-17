from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from dss.core.forms.models import FormRecords


class SessionState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    forms: FormRecords = {}
