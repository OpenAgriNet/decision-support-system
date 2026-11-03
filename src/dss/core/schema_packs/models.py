from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FieldSpec:
    """What one field is, merged across everything that declares it."""

    type: str
    required: bool
    enum: tuple[str, ...] = field(default=())
    # JSON Schema's `format`, when the pack declares one. `type` alone does not
    # say enough: `arrivalDate` is a `string` like 85 other fields, and
    # `format: date` is the half that says an ISO date rather than free text.
    format: str | None = None


@dataclass(frozen=True)
class SchemaPackSkipped:
    """A pack was malformed and left out of the index.

    network-specs is an external checkout, so one third-party commit must not
    blind every other capability. Skipping keeps the system serving — but a
    skipped pack looks exactly like a capability nobody offers, so this has to
    reach an operator rather than pass silently.
    """

    pack_name: str
    reason: str


@dataclass(frozen=True)
class SchemaPackFiles:
    """Raw file content for one schema pack — before any parsing.

    Only the three files the index needs. ``vocab.jsonld``, ``renderer.json``,
    and ``context.jsonld`` are never fetched.
    """

    pack_name: str
    version: str
    profile_json: str
    attributes_yaml: str
    examples_json: tuple[str, ...]
    # The one derived value here, and it is derived by the reading layer on
    # purpose: a pack's fields are split across its own `attributes.yaml` and
    # `AgricultureResource`'s, and resolving that `$ref` needs a path, which
    # this model does not carry. Empty when the shared file was not readable.
    flattened_fields: dict[str, FieldSpec] = field(default_factory=dict)
