from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from dss.core.forms.models import FormContext, FormDefinition
from dss.core.provider_discovery.models import SchemaPackSkipped


class FormLookup(Protocol):
    """What the orchestrator and validation need from the registry. The
    registry built from the packs satisfies it by shape."""

    def find(self, context: FormContext) -> FormDefinition | None: ...


class FormRegistry:
    """The forms built from the API packs, read once at boot. Raises
    ``ValueError`` on two forms for one context: the builder picks, not this."""

    def __init__(
        self,
        forms: Iterable[FormDefinition] = (),
        *,
        skipped: tuple[SchemaPackSkipped, ...] = (),
    ) -> None:
        self._forms: dict[FormContext, FormDefinition] = {}
        for form in forms:
            if form.context in self._forms:
                raise ValueError(f"two forms for {form.context}")
            self._forms[form.context] = form
        self._skipped = skipped

    def find(self, context: FormContext) -> FormDefinition | None:
        return self._forms.get(context)

    def __len__(self) -> int:
        return len(self._forms)

    @property
    def skipped(self) -> tuple[SchemaPackSkipped, ...]:
        """Packs and forms left out, for an operator: a missing form looks
        exactly like a scheme nobody serves."""

        return self._skipped
