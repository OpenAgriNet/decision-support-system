from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Container, Mapping, Sequence
from dataclasses import dataclass
from uuid import uuid4

import yaml

from dss.core.channel.models import ClarificationText, ComposedAnswer
from dss.core.forms.models import (
    ActionField,
    ActionSchema,
    FieldDefinition,
    FieldError,
    FieldErrorCode,
    FieldErrorEntry,
    FieldRules,
    FieldSource,
    FieldValue,
    FormBlock,
    FormContext,
    FormDefinition,
    FormErrorBlock,
    FormErrors,
    FormSubmission,
    JsonScalar,
    PiiClass,
    ValidSubmission,
)
from dss.core.forms.registry import FormLookup, FormRegistry
from dss.core.provider_discovery.index import PACK_DEFECTS, is_api_pack
from dss.core.provider_discovery.models import (
    ProviderCapability,
    SchemaPackFiles,
    SchemaPackSkipped,
)
from dss.core.shared.models import TextBlock
from dss.core.shared.schema import resolve_schema

_ACTION_MAP = "x-oan-schema-by-action"
# A desk's catalog entry. Discovery reads it; nobody fills it in.
_CATALOG_ACTION = "catalog/publish"
_SCHEME_PATH = "scheme.code"
_PROVIDER_PREFIX = "provider."
_PII_MARK = "x-oan-pii"
_JSONLD = "x-jsonld"
# The DSS cannot run this OTP step yet, so a scheme that needs it is not served.
_OTP_CHALLENGE_ACTION = "init"


@dataclass(frozen=True)
class _ParsedPack:
    pack_name: str
    schemas: tuple[ActionSchema, ...]


@dataclass(frozen=True)
class _SchemeKey:
    scheme: str
    category: str

    @classmethod
    def of(cls, context: FormContext) -> _SchemeKey:
        return cls(context.scheme, context.category)


@dataclass(frozen=True)
class _Registration:
    schemas: tuple[ActionSchema, ...]
    skipped: tuple[SchemaPackSkipped, ...]


def offer_form(definition: FormDefinition) -> FormBlock:
    """A new offer each call: two offers of one form type are two forms to
    fill in, and a submission names the offer it answers."""

    return FormBlock(form_id=uuid4().hex, form=definition)


def build_form_registry(packs: Sequence[SchemaPackFiles]) -> FormRegistry:
    """Never raises. A pack it cannot read, or a form the DSS cannot serve
    yet, is left out and named in ``skipped``. A catalog pack is ignored. Of
    two forms for one context, the first pack's wins."""

    pack_outcomes = tuple(_parse_api_pack_or_skip(pack) for pack in packs)
    parsed = tuple(o for o in pack_outcomes if isinstance(o, _ParsedPack))
    unreadable = tuple(o for o in pack_outcomes if isinstance(o, SchemaPackSkipped))
    registration = _register_servable(
        parsed, challenged=_schemes_needing_a_challenge(parsed)
    )
    return FormRegistry(
        registration.schemas, skipped=(*unreadable, *registration.skipped)
    )


def _parse_api_pack_or_skip(
    pack: SchemaPackFiles,
) -> _ParsedPack | SchemaPackSkipped | None:
    """None for a catalog pack: nothing is wrong with it, it holds no form."""

    try:
        if not is_api_pack(pack):
            return None
        return _ParsedPack(pack.pack_name, parse_api_pack(pack))
    except PACK_DEFECTS as exc:
        return SchemaPackSkipped(pack.pack_name, f"{type(exc).__name__}: {exc}")


def _schemes_needing_a_challenge(
    parsed: Sequence[_ParsedPack],
) -> frozenset[_SchemeKey]:
    return frozenset(
        _SchemeKey.of(schema.context)
        for pack in parsed
        for schema in pack.schemas
        if schema.context.action == _OTP_CHALLENGE_ACTION
    )


def _register_servable(
    parsed: Sequence[_ParsedPack], *, challenged: frozenset[_SchemeKey]
) -> _Registration:
    served: dict[FormContext, ActionSchema] = {}
    skipped: list[SchemaPackSkipped] = []
    for pack in parsed:
        for schema in pack.schemas:
            reason = _why_unserved(schema.context, challenged=challenged, taken=served)
            if reason is None:
                served[schema.context] = schema
            else:
                skipped.append(SchemaPackSkipped(pack.pack_name, reason))
    return _Registration(tuple(served.values()), tuple(skipped))


def _why_unserved(
    context: FormContext,
    *,
    challenged: frozenset[_SchemeKey],
    taken: Container[FormContext],
) -> str | None:
    if _SchemeKey.of(context) in challenged:
        return (
            f"{context}: needs an {_OTP_CHALLENGE_ACTION} step the DSS cannot run yet"
        )
    if context in taken:
        return f"{context}: a form for this context exists"
    return None


def parse_api_pack(pack: SchemaPackFiles) -> tuple[ActionSchema, ...]:
    """One ActionSchema per leaf, for every action but ``catalog/publish``.

    Raises ``ValueError``, ``KeyError``, ``TypeError`` or ``yaml.YAMLError``
    on a pack it cannot read — the same defects ``PACK_DEFECTS`` names.
    """

    try:
        return _read_pack(yaml.safe_load(pack.attributes_yaml))
    except (AttributeError, RecursionError) as exc:
        # A list where a mapping belongs fails on `.get`; a `$ref` cycle never
        # ends. Callers skip a bad pack on PACK_DEFECTS, which names neither.
        raise TypeError(f"malformed API pack {pack.pack_name}: {exc!r}") from exc


def _read_pack(document: dict) -> tuple[ActionSchema, ...]:
    schemas = document["components"]["schemas"]
    category = _find_category_name(schemas)
    root = schemas[category]
    return tuple(
        schema
        for action, per_call_name in _served_actions(root).items()
        for schema in _read_action(
            schemas[per_call_name],
            action=action,
            category=category,
            jsonld=root.get(_JSONLD, {}),
            document=document,
        )
    )


def _find_category_name(schemas: Mapping[str, dict]) -> str:
    roots = [name for name, schema in schemas.items() if _ACTION_MAP in schema]
    if len(roots) != 1:
        raise ValueError(f"expected one schema with {_ACTION_MAP}, found {len(roots)}")
    return roots[0]


def _served_actions(root: dict) -> dict[str, str]:
    return {
        action: per_call_name
        for action, per_call_name in root[_ACTION_MAP].items()
        if action != _CATALOG_ACTION
    }


def _read_action(
    per_call: dict, *, action: str, category: str, jsonld: dict, document: dict
) -> tuple[ActionSchema, ...]:
    return tuple(
        _read_leaf(
            leaf_ref, action=action, category=category, jsonld=jsonld, document=document
        )
        for leaf_ref in per_call["oneOf"]
    )


def _read_leaf(
    leaf_ref: dict, *, action: str, category: str, jsonld: dict, document: dict
) -> ActionSchema:
    leaf = _resolve(leaf_ref, document)
    must_send = tuple(
        _read_field(path, definition)
        for path in _must_send_paths(leaf)
        if (definition := _definition_at(leaf, path, document)) is not None
    )
    context = FormContext(
        scheme=_scheme_of(leaf, document), category=category, action=action
    )
    return ActionSchema(context=context, must_send=must_send, jsonld=jsonld)


def _must_send_paths(leaf: dict) -> list[str]:
    paths = leaf["x-oan-fields"]["mustSend"]
    if not isinstance(paths, list):
        raise TypeError(f"mustSend must be a list, got {type(paths).__name__}")
    return paths


def _definition_at(schema: dict, path: str, document: dict) -> dict | None:
    """The resolved schema at a dotted path, or ``None`` if the leaf does not
    declare it — a mustSend path only counts where properties declare it."""

    node = schema
    for segment in path.split("."):
        child = node.get("properties", {}).get(segment)
        if not isinstance(child, dict):
            return None
        node = _resolve(child, document)
    return node


def _scheme_of(leaf: dict, document: dict) -> str:
    definition = _definition_at(leaf, _SCHEME_PATH, document) or {}
    allowed = _allowed_values(definition)
    if len(allowed) != 1 or not isinstance(allowed[0], str):
        raise ValueError(f"leaf pins no single {_SCHEME_PATH}: {allowed}")
    return allowed[0]


def _read_field(path: str, definition: dict) -> ActionField:
    allowed = _allowed_values(definition)
    source = _source_of(path, allowed)
    if source is FieldSource.PROVIDER and path not in _PROVIDER_VALUES:
        # A pack defect, caught here so it stops the boot, not a filing.
        raise ValueError(f"{path}: no provider value to fill it from")
    return ActionField(
        path=path,
        source=source,
        allowed_values=allowed,
        pattern=_python_pattern(path, definition.get("pattern")),
        min_length=definition.get("minLength"),
        max_length=definition.get("maxLength"),
        pii_class=definition.get(_PII_MARK, {}).get("class"),
    )


def _python_pattern(path: str, pattern: str | None) -> str | None:
    """A pack writes ECMA 262; the DSS checks with Python's ``re``. A pattern
    the one cannot read fails the pack here, never a farmer's submission."""

    if pattern is None:
        return None
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValueError(
            f"{path}: pattern {pattern!r} is not Python syntax: {exc}"
        ) from exc
    return pattern


def _allowed_values(definition: dict) -> tuple[JsonScalar, ...]:
    if "enum" in definition:
        return tuple(definition["enum"])
    if "const" in definition:
        return (definition["const"],)
    return ()


def _source_of(path: str, allowed_values: tuple[JsonScalar, ...]) -> FieldSource:
    if len(allowed_values) == 1:
        return FieldSource.FIXED
    if path.startswith(_PROVIDER_PREFIX):
        return FieldSource.PROVIDER
    return FieldSource.FARMER


def _resolve(node: dict, document: dict) -> dict:
    # An API pack never points into another file, so there is no shared one.
    return resolve_schema(node, home=document, shared=None)


def validate_submission(
    submission: FormSubmission, forms: FormLookup
) -> ValidSubmission | FormErrors:
    definition = forms.find(submission.context)
    if definition is None:
        return FormErrors(
            form_id=submission.form_id,
            context=submission.context,
            submission_id=submission.submission_id,
            definition=None,
        )
    sent_twice = _names_sent_twice(submission.values)
    values = {value.name: value.value for value in submission.values}
    field_errors = (
        *_broken_rules(definition, values, skipping=sent_twice),
        *_errors_of(FieldErrorCode.DUPLICATE, sent_twice),
        *_errors_of(
            FieldErrorCode.UNKNOWN_FIELD,
            _names_not_on_the_form(definition, values, skipping=sent_twice),
        ),
    )
    if field_errors:
        return FormErrors(
            form_id=submission.form_id,
            context=submission.context,
            submission_id=submission.submission_id,
            definition=definition,
            field_errors=field_errors,
        )
    return ValidSubmission(
        definition=definition, submission_id=submission.submission_id, values=values
    )


def _errors_of(code: FieldErrorCode, names: Sequence[str]) -> tuple[FieldError, ...]:
    return tuple(FieldError(name=name, code=code) for name in names)


def _names_not_on_the_form(
    definition: FormDefinition, values: Mapping[str, str], *, skipping: Container[str]
) -> tuple[str, ...]:
    return tuple(
        name
        for name in values
        if name not in skipping and definition.field(name) is None
    )


def _names_sent_twice(values: Sequence[FieldValue]) -> tuple[str, ...]:
    sightings = Counter(value.name for value in values)
    return tuple(name for name, count in sightings.items() if count > 1)


def _broken_rules(
    definition: FormDefinition, values: Mapping[str, str], *, skipping: Container[str]
) -> tuple[FieldError, ...]:
    checked = (
        _first_broken_rule(field, values.get(field.name))
        for field in definition.fields
        if field.name not in skipping
    )
    return tuple(error for error in checked if error is not None)


def _is_off_pattern(rules: FieldRules, value: str) -> bool:
    # `search`, not `fullmatch`: JSON Schema patterns anchor themselves.
    return rules.pattern is not None and re.search(rules.pattern, value) is None


def _is_too_short(rules: FieldRules, value: str) -> bool:
    return rules.min_length is not None and len(value) < rules.min_length


def _is_too_long(rules: FieldRules, value: str) -> bool:
    return rules.max_length is not None and len(value) > rules.max_length


def _is_not_an_option(rules: FieldRules, value: str) -> bool:
    return bool(rules.options) and all(
        option.value != value for option in rules.options
    )


# Spec order for a value that is there: the first rule it breaks is reported.
_RULES: tuple[tuple[FieldErrorCode, Callable[[FieldRules, str], bool]], ...] = (
    (FieldErrorCode.PATTERN, _is_off_pattern),
    (FieldErrorCode.MIN_LENGTH, _is_too_short),
    (FieldErrorCode.MAX_LENGTH, _is_too_long),
    (FieldErrorCode.NOT_AN_OPTION, _is_not_an_option),
)


def _first_broken_rule(field: FieldDefinition, value: str | None) -> FieldError | None:
    if value is None or not value.strip():
        if field.rules.required:
            return FieldError(name=field.name, code=FieldErrorCode.REQUIRED)
        return None
    return next(
        (
            FieldError(name=field.name, code=code)
            for code, is_broken in _RULES
            if is_broken(field.rules, value)
        ),
        None,
    )


def free_text_of(valid: ValidSubmission) -> tuple[str, ...]:
    """What a farmer wrote in prose, in field order: the part of a form that
    is moderated like a question."""

    return tuple(
        valid.values[field.name]
        for field in valid.definition.fields
        if field.pii_class == PiiClass.FREETEXT and field.name in valid.values
    )


# What the audit record and the trace hold in place of a farmer's value.
# Values are withheld, not tagged: tagging a form by the pack's PII mark is
# its own story. Only the free text is tagged, and only to be moderated.
WITHHELD = "[withheld]"


def withhold_values(submission: FormSubmission) -> FormSubmission:
    return submission.model_copy(
        update={
            "values": tuple(
                FieldValue(name=value.name, value=WITHHELD)
                for value in submission.values
            )
        }
    )


def answer_for_form_errors(
    errors: FormErrors, text: ClarificationText
) -> ComposedAnswer:
    """Text only for a form nobody serves: there is no form to hand back."""

    if errors.definition is None:
        return answer_for_unknown_form(text)
    return ComposedAnswer(
        content=(
            TextBlock(text=text.form_has_errors),
            form_error_block(errors, errors.definition, text),
        )
    )


def answer_for_unknown_form(text: ClarificationText) -> ComposedAnswer:
    return ComposedAnswer(content=(TextBlock(text=text.unknown_form),))


def form_error_block(
    errors: FormErrors, definition: FormDefinition, text: ClarificationText
) -> FormErrorBlock:
    return FormErrorBlock(
        form_id=errors.form_id,
        context=errors.context,
        submission_id=errors.submission_id,
        fields=tuple(
            _error_entry(error, definition.field(error.name), text)
            for error in errors.field_errors
        ),
    )


_TEMPLATE_FOR: Mapping[FieldErrorCode, Callable[[ClarificationText], str]] = {
    FieldErrorCode.REQUIRED: lambda text: text.field_required,
    FieldErrorCode.PATTERN: lambda text: text.field_pattern,
    FieldErrorCode.MIN_LENGTH: lambda text: text.field_min_length,
    FieldErrorCode.MAX_LENGTH: lambda text: text.field_max_length,
    FieldErrorCode.NOT_AN_OPTION: lambda text: text.field_not_an_option,
    FieldErrorCode.DUPLICATE: lambda text: text.field_duplicate,
    FieldErrorCode.UNKNOWN_FIELD: lambda text: text.field_unknown,
}


def _error_entry(
    error: FieldError, field: FieldDefinition | None, text: ClarificationText
) -> FieldErrorEntry:
    # A name the form does not have has no label; the farmer sees the name.
    label = field.label if field is not None else ""
    rules = field.rules if field is not None else FieldRules()
    message = _TEMPLATE_FOR[error.code](text).format(
        label=label or error.name, min=rules.min_length, max=rules.max_length
    )
    return FieldErrorEntry(name=error.name, label=label, error=message)


# What a `provider.*` path is filled from: the chosen provider, as discovery
# returned it.
_PROVIDER_VALUES: Mapping[str, Callable[[ProviderCapability], str]] = {
    "provider.id": lambda provider: provider.provider_id,
    "provider.descriptor.name": lambda provider: provider.provider_name,
}


def build_action_attributes(
    schema: ActionSchema, provider: ProviderCapability, values: Mapping[str, str]
) -> dict:
    """The body of one call, every `mustSend` path filled by its source: the
    pack's fixed values, the chosen provider, the farmer's form."""

    body: dict = dict(schema.jsonld)
    for field in schema.must_send:
        _set_at(body, field.path, _value_for(field, provider, values))
    return body


def _value_for(
    field: ActionField, provider: ProviderCapability, values: Mapping[str, str]
) -> JsonScalar:
    match field.source:
        case FieldSource.FIXED:
            return field.allowed_values[0]
        case FieldSource.PROVIDER:
            return _PROVIDER_VALUES[field.path](provider)
        case FieldSource.FARMER:
            return values[field.path]


def _set_at(body: dict, path: str, value: JsonScalar) -> None:
    *parents, leaf = path.split(".")
    node = body
    for segment in parents:
        node = node.setdefault(segment, {})
    node[leaf] = value


def candidates_for_scheme(
    candidates: Sequence[ProviderCapability], scheme_code: str
) -> tuple[ProviderCapability, ...]:
    """The desks that advertise the form's scheme. Discovery indexes every
    grievance desk under one `@type`, so the scheme is told apart here, in
    code, before the model sees any candidate."""

    return tuple(
        candidate
        for candidate in candidates
        if _advertised_at(candidate, _SCHEME_PATH) == scheme_code
    )


def _advertised_at(candidate: ProviderCapability, path: str) -> object:
    node: object = candidate.advertised
    for segment in path.split("."):
        if not isinstance(node, Mapping):
            return None
        node = node.get(segment)
    return node
