from __future__ import annotations

import json
import re
from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from dss.core.shared.models import OutputBlock, TurnInput


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

    def __str__(self) -> str:
        return f"{self.scheme}/{self.category}/{self.action}"


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


class PiiClass(StrEnum):
    """The classes network-specs marks a field with (`x-oan-pii.class`)."""

    CONTACT = "contact"
    IDENTIFIER = "identifier"
    FREETEXT = "freetext"
    CREDENTIAL = "credential"


class FieldDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str  # the pack's path, e.g. "grievance.description"
    label: str
    kind: FieldKind
    rules: FieldRules = FieldRules()
    # A plain string, not `PiiClass`: a class this code does not know yet must
    # not make the pack unreadable.
    pii_class: str | None = None


class FormDefinition(BaseModel):
    """One form as the registry holds it, cut from one API schema pack for one
    action. ``fields`` are the farmer's; the catalog supplies the rest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    context: FormContext
    fields: tuple[FieldDefinition, ...]

    def field(self, name: str) -> FieldDefinition | None:
        return next((field for field in self.fields if field.name == name), None)


type JsonScalar = str | int | float | bool


class FieldSource(StrEnum):
    """Who supplies a path's value on the call."""

    FIXED = "fixed"
    PROVIDER = "provider"
    FARMER = "farmer"


class ActionField(BaseModel):
    """One mustSend path of a leaf, and what the pack says about its value."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    source: FieldSource
    # The pack's `enum`, or its `const` alone, with JSON's own types kept.
    allowed_values: tuple[JsonScalar, ...] = ()
    pattern: str | None = None
    min_length: int | None = None
    max_length: int | None = None
    pii_class: str | None = None


class ActionSchema(BaseModel):
    """What one call needs in its attributes, read from one leaf of an API
    pack. ``must_send`` keeps the leaf's order."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    context: FormContext
    must_send: tuple[ActionField, ...]

    def form(self) -> FormDefinition:
        """The fields the farmer fills in; the rest come from the schema or
        from discovery."""

        return FormDefinition(
            context=self.context,
            fields=tuple(
                _to_form_field(field)
                for field in self.must_send
                if field.source is FieldSource.FARMER
            ),
        )


# Above this a farmer is writing prose, not filling in a value. Our number: the
# pack gives no layout hints.
_LONGEST_ONE_LINE_ANSWER = 255
# camelCase, an acronym before a word (`PMKISANId`), letters before digits.
_WORD_BREAK = re.compile(
    r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=[A-Za-z])(?=[0-9])"
)


def _to_form_field(field: ActionField) -> FieldDefinition:
    return FieldDefinition(
        name=field.path,
        label=_label_for(field.path),
        kind=_kind_for(field),
        rules=FieldRules(
            required=True,
            pattern=field.pattern,
            min_length=field.min_length,
            max_length=field.max_length,
            options=tuple(_option_for(value) for value in field.allowed_values),
        ),
        pii_class=field.pii_class,
    )


def _kind_for(field: ActionField) -> FieldKind:
    if field.allowed_values:
        return FieldKind.SELECT
    if field.max_length is not None and field.max_length > _LONGEST_ONE_LINE_ANSWER:
        return FieldKind.TEXTAREA
    return FieldKind.TEXT


def _option_for(value: JsonScalar) -> Option:
    # Written as JSON writes it, so `true` stays `true`, not Python's `True`.
    text = value if isinstance(value, str) else json.dumps(value)
    return Option(value=text, label=text)


def _label_for(path: str) -> str:
    """``"grievance.description"`` → "Grievance description". The pack has no
    titles yet."""

    words = " ".join(
        word if word.isupper() else word.lower()
        for segment in path.split(".")
        for word in _WORD_BREAK.split(segment)
    )
    return words[:1].upper() + words[1:]


class FormBlock(OutputBlock):
    """One offer of a form to one farmer. ``form_id`` names the offer, not the
    form's type; ``form.context`` does that."""

    form_id: str
    form: FormDefinition


class FieldValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    value: str


class FormSubmission(TurnInput):
    form_id: str
    context: FormContext
    submission_id: str
    values: tuple[FieldValue, ...]


class FieldErrorCode(StrEnum):
    REQUIRED = "required"
    PATTERN = "pattern"
    MIN_LENGTH = "min_length"
    MAX_LENGTH = "max_length"
    NOT_AN_OPTION = "not_an_option"
    DUPLICATE = "duplicate"
    UNKNOWN_FIELD = "unknown_field"


class FieldError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    code: FieldErrorCode


class FormErrors(BaseModel):
    """``definition`` is the form the values were checked against; ``None``
    when nobody serves the context, and then ``field_errors`` is empty."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    form_id: str
    context: FormContext
    submission_id: str
    definition: FormDefinition | None
    field_errors: tuple[FieldError, ...] = ()


class ValidSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    definition: FormDefinition
    submission_id: str
    values: Mapping[str, str]


class FieldErrorEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    label: str
    error: str


class FormErrorBlock(OutputBlock):
    """Tells the UI the form is still the farmer's."""

    form_id: str
    context: FormContext
    submission_id: str
    fields: tuple[FieldErrorEntry, ...] = ()
