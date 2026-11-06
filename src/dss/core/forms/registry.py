from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from dss.core.forms.models import (
    ActionSchema,
    FormContext,
    FormDefinition,
    ProviderAction,
)
from dss.core.schema_packs.models import SchemaPackSkipped


class FormLookup(Protocol):
    """What the orchestrator and validation need from the registry. The
    registry built from the packs satisfies it by shape."""

    def find(self, context: FormContext) -> FormDefinition | None: ...

    def action_schema(self, context: FormContext) -> ActionSchema | None: ...


class FormRegistry:
    """The action schemas read from the API packs at boot, and the forms cut
    from them. Raises ``ValueError`` on two schemas for one context: the
    builder picks, not this."""

    def __init__(
        self,
        schemas: Iterable[ActionSchema] = (),
        *,
        skipped: tuple[SchemaPackSkipped, ...] = (),
    ) -> None:
        self._schemas: dict[FormContext, ActionSchema] = {}
        self._forms: dict[FormContext, FormDefinition] = {}
        for schema in schemas:
            if schema.context in self._schemas:
                raise ValueError(f"two forms for {schema.context}")
            self._schemas[schema.context] = schema
            for form in _forms_from(schema):
                # Support and status wait for one init: the first one's
                # fields make its form.
                self._forms.setdefault(form.context, form)
        self._skipped = skipped

    def find(self, context: FormContext) -> FormDefinition | None:
        return self._forms.get(context)

    def action_schema(self, context: FormContext) -> ActionSchema | None:
        return self._schemas.get(context)

    def __len__(self) -> int:
        return len(self._schemas)

    @property
    def skipped(self) -> tuple[SchemaPackSkipped, ...]:
        """Packs and forms left out, for an operator: a missing form looks
        exactly like a scheme nobody serves."""

        return self._skipped


def _forms_from(schema: ActionSchema) -> tuple[FormDefinition, ...]:
    # What init sends comes from the details form; its own form is what the
    # farmer fills once it has run.
    if schema.context.action == ProviderAction.INIT:
        return ()
    details = FormDefinition.from_fields(schema.context, schema.request_fields())
    if not schema.needs_init():
        return (details,)
    init = schema.context.model_copy(update={"action": ProviderAction.INIT.value})
    return (details, FormDefinition.from_fields(init, schema.init_fields()))
