from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from dss.core.shared.models import OutputBlock


class FormContext(BaseModel):
    """The form's type. Sent with the form, copied back with the submission."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scheme: str
    category: str
    action: str

    # Pydantic adds the same hash to frozen models; declared so a type checker
    # sees a context can be a dict key.
    def __hash__(self) -> int:
        return hash((self.scheme, self.category, self.action))


class FieldKind(StrEnum):
    TEXT = "text"
    TEXTAREA = "textarea"
    SELECT = "select"
    SECRET = "secret"  # noqa: S105  # a kind of field, not a password


class Option(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    value: str
    label: str


class FieldRules(BaseModel):
    """What the pack lets a client check before sending, and the DSS after."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    required: bool = False
    pattern: str | None = None
    min_length: int | None = None
    max_length: int | None = None
    options: tuple[Option, ...] = ()


class FieldDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str  # the pack's path, e.g. "grievance.description"
    label: str
    kind: FieldKind
    rules: FieldRules = FieldRules()


class FormBlock(OutputBlock):
    """A form for the farmer to fill. ``form_id`` names this one offer, not
    the form's type; ``context`` does that."""

    form_id: str
    context: FormContext
    fields: tuple[FieldDefinition, ...]
