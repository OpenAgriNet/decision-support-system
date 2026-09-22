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

from pydantic import BaseModel, ConfigDict, Field, model_serializer
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
    """`RequestContext` in the contract. `additionalProperties: false`, so a
    field this does not name is rejected outright."""

    id: Literal["api.dss.turn"]
    timestamp: datetime
    session_id: str
    transaction_id: str  # required; echoed back as `traceId`
    version: str | None = None
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
    """Response side of the envelope.

    The contract requires `id`, `version`, `timestamp`, `messageId`, `sessionId`
    and `traceId`, and allows additional properties — so `resMessageId` and
    `sequenceNumber` ride along. `sequenceNumber` is present on a stream and
    absent on a single JSON response.
    """

    id: Literal["api.dss.turn"] = "api.dss.turn"
    version: str  # the concrete DSS release that handled the turn
    timestamp: datetime
    message_id: str
    session_id: str
    trace_id: str
    res_message_id: str
    # Echoed for the caller's own correlation. Identical to `traceId` today —
    # the contract's §3 example carries both, and `ResponseContext` allows
    # additional properties.
    transaction_id: str | None = None
    sequence_number: int | None = Field(default=None, ge=1)


class Outcome(_WireOut):
    """The contract requires all three fields, and `cause` is nullable.

    `cause` therefore has to be emitted as `null` rather than omitted. Frames are
    dumped with `exclude_none=True` (so optional fields stay out), which would
    drop it — hence the explicit serializer. Every field the contract marks
    required *and* nullable needs this treatment.
    """

    status: str
    confidence: int = Field(ge=0, le=100)
    cause: str | None = None

    @model_serializer(mode="wrap")
    def _keep_null_cause(self, handler):  # type: ignore[no-untyped-def]
        data = handler(self)
        data.setdefault("cause", None)
        return data


class Annotation(_WireOut):
    """A citation over a span of `text`.

    `startIndex` and `endIndex` are **Unicode code points** — Python string
    indices.

    The contract does not name a unit in prose, but its own §3 example resolves
    it: for "इस सप्ताह आनंद मंडी में गेहूं का भाव ₹2,275 प्रति क्विंटल है।" it gives
    `end_index: 61`, which is that sentence's code-point length. The same
    sentence is 151 bytes in UTF-8, so the example rules bytes out.

    Code points and UTF-16 units agree for Devanagari and Tamil (both BMP), so
    the choice only diverges on non-BMP characters such as emoji. Worth writing
    into the contract explicitly, since an example is weaker than a rule.
    """

    type: Literal["url_citation"] = "url_citation"
    source_id: str
    start_index: int = Field(ge=0)
    end_index: int = Field(ge=0)
    url: str | None = None
    source_name: str | None = None


class OutputText(_WireOut):
    type: Literal["text"] = "text"
    text: str
    annotations: list[Annotation] = []


class OutputTextDelta(_WireOut):
    """A piece of an answer still being written.

    Deliberately thinner than `OutputText`: no `annotations`, because a block
    mid-write has no end index for a citation to span. Provenance arrives with
    the `OutputText` that follows, which is the first point the block is final.

    A consumer concatenates these. Pieces land on whatever boundary the model
    produced — one may end mid-word, mid-number or mid-citation-marker.
    """

    type: Literal["output_text_delta"] = "output_text_delta"
    text: str


class OutputRefusal(_WireOut):
    type: Literal["refusal"] = "refusal"
    text: str


class Source(_WireOut):
    id: str
    name: str
    kind: str
    url: str | None = None


class TurnError(_WireOut):
    """Present when a dependency failed. `code` repeats `outcome.cause` — the
    contract carries both, so both are sent."""

    code: str
    message: str
    retryable: bool
    retry_after_seconds: int | None = None


class ResponseMessage(_WireOut):
    """`outcome` is absent until the turn ends, so `turn.created`,
    `claim.delta` and `claim.completed` carry content without implying a
    result."""

    outcome: Outcome | None = None
    content: list[OutputText | OutputTextDelta | OutputRefusal] = []
    sources: list[Source] = []
    error: TurnError | None = None


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
