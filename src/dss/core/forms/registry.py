from __future__ import annotations

from typing import Protocol

from dss.core.forms.models import FormContext, FormDefinition


class FormLookup(Protocol):
    """What the orchestrator and validation need from the registry. The
    registry built from the packs satisfies it by shape."""

    def find(self, context: FormContext) -> FormDefinition | None: ...
