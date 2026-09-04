"""The wire models. Field names live here and nowhere else.

**The wire is camelCase** (`docs/api-contracts/openapi.yaml`); Python stays
snake_case (`CONVENTIONS.md`). Aliases bridge the two here and only here, so
`mapping.py` and everything inward never sees a camelCase name.

`extra="forbid"` throughout: the contract sets `additionalProperties: false`, and
a typo in a caller's payload must fail loudly rather than be silently dropped.

The two bases differ on one setting, deliberately:

- `_WireIn` sets `populate_by_name=False`, so **only** camelCase is accepted. A
  snake_case key is an unknown field, which is what the contract says it is.
- `_WireOut` sets `populate_by_name=True`, because `mapping.py` constructs these
  by field name. They are serialized with `by_alias=True`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class _WireIn(BaseModel):
    """Inbound. camelCase only."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=False, extra="forbid"
    )


class _WireOut(BaseModel):
    """Outbound. Built by field name, emitted as camelCase."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="forbid"
    )


class Context(_WireIn):
    id: Literal["api.dss.turn"]
    envelope_version: str
    timestamp: datetime
    session_id: str
    transaction_id: str | None = None
    message_id: str | None = None


class Identity(_WireIn):
    type: Literal["identity"]
    user_id: str


class TextContent(_WireIn):
    type: Literal["text"]
    text: str


class InputMessage(_WireIn):
    role: Literal["user", "assistant"]
    content: list[Annotated[TextContent, Field(discriminator="type")]] = Field(
        min_length=1
    )


class Geometry(_WireIn):
    type: Literal["Point"]
    coordinates: tuple[float, float]  # [lon, lat] — the order lives here


class Location(_WireIn):
    region: str | None = None
    area: str | None = None
    geometry: Geometry | None = None


class ResponseSpec(_WireIn):
    max_characters: int | None = Field(default=None, ge=1)


class Attributes(_WireIn):
    source_language: str
    target_language: str
    channel: Literal["web", "whatsapp", "voice", "sms"]
    location: Location | None = None
    response: ResponseSpec | None = None


class Message(_WireIn):
    input: list[InputMessage] = Field(min_length=1)
    user_context: list[Annotated[Identity, Field(discriminator="type")]] = []
    attributes: Attributes


class TurnRequest(_WireIn):
    context: Context
    message: Message


class ResponseContext(_WireOut):
    """Response side of the envelope. `sequence_number` is present on a stream
    and absent on a single JSON response."""

    id: Literal["api.dss.turn"] = "api.dss.turn"
    envelope_version: str
    dss_release: str
    timestamp: datetime
    session_id: str
    trace_id: str
    response_message_id: str
    transaction_id: str | None = None
    message_id: str | None = None
    sequence_number: int | None = None


class Outcome(_WireOut):
    status: str
    cause: str | None = None
    retry_after_seconds: int | None = None


class OutputText(_WireOut):
    type: Literal["text"] = "text"
    text: str
    source_ids: list[str] = []


class OutputRefusal(_WireOut):
    type: Literal["refusal"] = "refusal"
    text: str


class Source(_WireOut):
    id: str
    name: str
    kind: str
    url: str | None = None


class ResponseMessage(_WireOut):
    """`outcome` is absent until the turn ends, so `turn.created` and
    `claim.completed` carry content without implying a result."""

    outcome: Outcome | None = None
    content: list[OutputText | OutputRefusal] = []
    sources: list[Source] = []


class TurnResponse(_WireOut):
    context: ResponseContext
    message: ResponseMessage


# Pydantic's own JSON Schema points at `#/$defs/X`. Inside an OpenAPI document a
# `#/...` reference resolves against the *document root*, which has no `$defs`,
# so every nested model would dangle. Generating with this template and
# publishing the definitions under `components.schemas` is what makes the
# document self-consistent.
REF_TEMPLATE = "#/components/schemas/{model}"

PUBLISHED = (TurnRequest, TurnResponse)


def component_schemas() -> dict[str, dict]:
    """Every wire model, flattened for `components.schemas`.

    Each root model is registered under its own name alongside the definitions it
    pulls in, so `#/components/schemas/TurnRequest` and everything beneath it
    resolve.
    """

    schemas: dict[str, dict] = {}
    for model in PUBLISHED:
        schema = model.model_json_schema(ref_template=REF_TEMPLATE)
        schemas.update(schema.pop("$defs", {}))
        schemas[model.__name__] = schema
    return schemas
