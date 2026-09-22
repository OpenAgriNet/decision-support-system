"""The envelope every outbound network call carries.

The OpenAgriNet v2.0 resource-discovery profile. Two actions — `discover` and
`select` — and participants named by `senderId` and `receiverId`. Written down
because other networks use similar words for different things.

Before this, the envelope was a dict literal in each of the two adapters, and
the protocol version was two private constants that happened to agree. The wire
is unchanged — the tests assert the exact dicts those literals produced.

**Two shapes, not one nullable one.** A discover names the schema it asks about
and no participants; a select names the participants and never a schema. One
model with everything optional would let a select ask about a schema, which the
network has no meaning for.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, field_serializer
from pydantic.alias_generators import to_camel


class NetworkAction(StrEnum):
    """What a message is for.

    Two, not four. `discover` and `select` answer synchronously — the reply
    comes back on the same call — so there is no separate `on_discover` or
    `on_select` message for the DSS to send or name.
    """

    DISCOVER = "discover"
    SELECT = "select"


class NetworkVersion(StrEnum):
    """The protocol version the DSS speaks.

    One member today. An enum rather than a constant because the next one is
    what makes this interesting: two versions in flight is the situation worth
    being able to write down.
    """

    V2_0_0 = "2.0.0"


@dataclass(frozen=True)
class NetworkSchemaContext:
    """Which schema a discover is asking about.

    Each URL is the pack's own `@context`, with the `@type` as a fragment. The
    base comes from the pack rather than from configuration — the pack is what
    states where its context lives — and the fragment is discover's own
    addition, naming which type in that context the query is about.
    """

    urls: tuple[str, ...]

    @classmethod
    def for_types(
        cls, types: tuple[str, ...], context_index: dict[str, str]
    ) -> Self | None:
        """The context for these `@type`s, or `None` when none were resolved.

        `None`, not an empty one. The contract takes either the filter or
        `schemaContext`, so an empty list would assert that no schema applies
        rather than that none was named (#52).
        """

        if not types:
            return None
        return cls(tuple(f"{context_index[name]}#{name}" for name in types))


class NetworkContext(BaseModel):
    """What every message carries, whichever direction it goes.

    Field names are snake_case here and camelCase on the wire, which is the
    same split the DSS's own HTTP surface uses. `extra="forbid"` is what makes
    the two subclasses actually separate rather than merely differently named.
    """

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="forbid", frozen=True
    )

    version: NetworkVersion = NetworkVersion.V2_0_0
    message_id: str
    transaction_id: str
    timestamp: str

    def to_wire(self) -> dict[str, Any]:
        """The dict that goes in the request body.

        `exclude_none` is load-bearing, not tidiness: it is what keeps an
        unnamed schema absent rather than null.
        """

        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


class DiscoverContext(NetworkContext):
    """A discover: which schema, and no participants.

    `action` is `Literal`, so it cannot be set to anything else — an envelope
    whose action disagreed with the endpoint it was posted to would be rejected
    somewhere far from the mistake.
    """

    action: Literal[NetworkAction.DISCOVER] = NetworkAction.DISCOVER
    schema_context: NetworkSchemaContext | None = None

    @field_serializer("schema_context")
    def _urls_only(self, value: NetworkSchemaContext | None) -> list[str] | None:
        # The wire takes a bare list of URLs; the type exists to keep the
        # fragment rule and the "absent, not empty" rule in one place.
        return None if value is None else list(value.urls)


class SelectContext(NetworkContext):
    """A select: who is asking whom, and no schema."""

    action: Literal[NetworkAction.SELECT] = NetworkAction.SELECT
    sender_id: str
    receiver_id: str
