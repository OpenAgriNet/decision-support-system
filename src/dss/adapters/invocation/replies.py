from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from dss.adapters.network_common import SourceReference, extract_source_reference
from dss.core.provider_discovery.models import DiscoveredAnswer


class _WireModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, frozen=True)


class _Resource(_WireModel):
    id: str
    resource_attributes: dict[str, Any]

    @field_validator("resource_attributes")
    @classmethod
    def _require_a_type(cls, attributes: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(attributes.get("@type"), str):
            raise ValueError("resourceAttributes has no @type string")
        return attributes


class _SelectCommitment(_WireModel):
    resources: list[_Resource]


class _SelectContract(_WireModel):
    commitments: list[_SelectCommitment]


class _SelectMessage(_WireModel):
    contract: _SelectContract


class _SelectReply(_WireModel):
    message: _SelectMessage


class _Commitment(_WireModel):
    commitment_attributes: dict[str, Any]


class _CommitmentContract(_WireModel):
    commitments: Annotated[list[_Commitment], Field(min_length=1)]


class _CommitmentMessage(_WireModel):
    contract: _CommitmentContract


class _CommitmentReply(_WireModel):
    message: _CommitmentMessage


class _Support(_WireModel):
    channels: Annotated[list[dict[str, Any]], Field(min_length=1)]


class _SupportMessage(_WireModel):
    support: _Support


class _SupportReply(_WireModel):
    message: _SupportMessage


def read_select_answers(
    body: object, *, provider_id: str, provider_name: str
) -> list[DiscoveredAnswer]:
    """The provider may assign its own resource id in the response — distinct
    from the discovered id we named in the request. A search resource answers
    with one id per result found, not the id that was selected.

    Every resource goes into ``attributes["resources"]``, even when there is
    only one, so the model always reads the same shape. A forecast sends one
    resource per day, and the model needs all of them.

    Resources are grouped by the document they came from, one answer per
    document. A forecast's five days share a source and stay one answer. A
    knowledge search returning passages from two documents becomes two, because
    a single answer carries a single source: lumping them together made every
    passage inherit the first one's, and half the advice was then cited to a
    document it never came from.

    Resources from every commitment are read, and a response committing to
    nothing yields no answers — an empty 200 is a real answer shape, not a body
    we failed to parse.
    """

    reply = _SelectReply.model_validate(body)
    grouped: dict[SourceReference | None, list[_Resource]] = {}
    for commitment in reply.message.contract.commitments:
        for resource in commitment.resources:
            source = extract_source_reference(resource.resource_attributes)
            grouped.setdefault(source, []).append(resource)

    return [
        _to_answer(
            resources,
            provider_id=provider_id,
            provider_name=provider_name,
            source=source,
        )
        for source, resources in grouped.items()
    ]


def read_support_channel(body: object) -> dict[str, Any]:
    """The reply rides where the request did: the first support channel."""

    return _SupportReply.model_validate(body).message.support.channels[0]


def read_commitment_attributes(body: object) -> dict[str, Any]:
    reply = _CommitmentReply.model_validate(body)
    return reply.message.contract.commitments[0].commitment_attributes


def _to_answer(
    resources: list[_Resource],
    *,
    provider_id: str,
    provider_name: str,
    source: SourceReference | None,
) -> DiscoveredAnswer:
    first = resources[0]
    return DiscoveredAnswer(
        provider_id=provider_id,
        provider_name=provider_name,
        capability=first.resource_attributes["@type"],
        resource_id=first.id,
        attributes={
            "resources": [
                {"id": resource.id, **resource.resource_attributes}
                for resource in resources
            ]
        },
        # Each resource keeps its own validity in `attributes`.
        validity=None,
        source_id=source.source_id if source else None,
        source_name=source.source_name if source else None,
        source_url=source.source_url if source else None,
    )
