"""Types produced and consumed by provider_discovery.

Data shapes only. Behaviour lives in service.py.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime
from enum import Enum

from dss.core.provider_discovery.schema_fields import FieldSpec

# A resource's `@type`, as a prefixed CURIE: `openagrinet:MandiPrice`. An open
# set, fed by whichever schema packs a deployment mounts — so never an enum.
# Called `capability` at most call sites, which is the DSS's word for the same
# thing seen from the planning side. A plain alias, like `NetworkTransactionID`
# in `core/shared/network.py`: it buys the name, not a runtime check.
NetworkSchemaType = str


@dataclass(frozen=True)
class Coverage:
    lat: float
    lon: float
    radius_m: int  # caller-supplied — config today, may vary per ask later


@dataclass(frozen=True)
class ProviderQuery:
    """A domain-level discovery query. No ask index, no trace id, no timestamp —
    those live in the envelope the client adapter builds.
    """

    capabilities: tuple[str, ...]
    # The Ask's subject category ("Weather"), which the JSONPath filter matches
    # on. `capabilities` carries the resolved @type values alongside it: those
    # name the envelope's `schemaContext`, which is what pins the query to a
    # specific resource type, so the filter itself does not repeat them.
    subject_category: str
    languages: tuple[str, ...]
    coverage: Coverage | None


@dataclass(frozen=True)
class ProviderCapability:
    """What discovery intends to use, not the provider's full inventory.

    ``kind`` is deferred until host-URL derivation is built.
    """

    provider_id: str
    provider_name: str
    capability: NetworkSchemaType
    resource_id: str  # names which resource `select` commits to
    observed_categories: tuple[str, ...] = ()  # the resource's own subjectCategories
    # The provider's own code from on_discover ("AGMARKNET-01"). Kept rather
    # than sent: the real select request names a provider by id and name only.
    # Optional because only `name` appears in every fixture.
    provider_code: str | None = None
    # The resource's own advertised vocabulary, verbatim from on_discover
    # ("supportedCommodities": [{"code": "78", "name": "Tomato"}, ...]). The
    # planner shows it to the model, which otherwise has to invent a code and
    # has nothing to check it against.
    #
    # An opaque map, not named fields: which fields a resource advertises is
    # pack-specific — MandiPrice names supportedCommodities, another pack
    # names something else — and one pack's vocabulary must not enter a type
    # four packs share. Same stance as DiscoveredAnswer.attributes.
    #
    # Mapping rather than dict: the dataclass is frozen, and a dict field
    # would still let a caller mutate what the network said.
    advertised: Mapping[str, object] = dataclass_field(default_factory=dict)


@dataclass(frozen=True)
class Validity:
    starts_at: datetime | None
    ends_at: datetime | None


@dataclass(frozen=True)
class DiscoveredAnswer:
    """A Direct resource's answer. ``attributes`` is the pack-specific
    ``resourceAttributes`` verbatim — provider_discovery doesn't know every
    pack's schema, so it passes them through rather than typing each one.
    """

    provider_id: str
    provider_name: str
    capability: NetworkSchemaType
    resource_id: str
    attributes: dict[str, object]
    validity: Validity | None
    # Who authored the data, from `resourceAttributes.source`. Not the same as
    # the provider: one relaying IMD still cites IMD. All optional — `source`
    # is optional on every pack that declares it, and `sourceUri` is dropped
    # here unless it is http(s), since a farmer cannot open a JSON-LD id.
    source_id: str | None = None
    source_name: str | None = None
    source_url: str | None = None


@dataclass(frozen=True)
class CapabilityUnresolved:
    """The (subject_category, action_type) pair resolves to zero @type values.

    Every valid pair should map to at least one @type, so this means our own
    mapping is incomplete — not that no provider serves this pair.
    """

    subject_category: str
    action_type: str


class FailureClass(Enum):
    TRANSIENT = "transient"
    DEFECT = "defect"


@dataclass(frozen=True)
class DiscoveryFailure:
    """A discovery call failed for one capability.

    ``provider_id`` is None for a whole-call failure — the only kind seen in
    a real response so far. A federated discovery service could plausibly
    report a per-provider partial failure inside one response; this field is
    ready for that but unverified against real data.
    """

    capability: NetworkSchemaType
    status_code: int
    failure_class: FailureClass
    provider_id: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class ExpiredAnswerDropped:
    """A Direct answer was outside its validity window and was dropped.

    Either side of the window: the name says "expired", but an answer whose
    ``startsAt`` has not arrived yet is dropped and reported here too. Not
    renamed because the event type is what the sinks consume.
    """

    provider_id: str
    capability: NetworkSchemaType
    resource_id: str
    had_fallback: bool  # an OnDemand capability existed on the same provider+capability


@dataclass(frozen=True)
class AskDiscoveryFailed:
    """A discovery call failed for one ask — mirrors the DiscoveryFailure."""

    ask_index: int
    capability: NetworkSchemaType
    failure_class: FailureClass
    status_code: int


@dataclass(frozen=True)
class AskUnservable:
    """An ask ended with no answer and no capability — the normal no_match path."""

    ask_index: int
    capabilities: tuple[str, ...]  # the @type values the query carried, for context


@dataclass(frozen=True)
class CategoryMappingDiverged:
    """A returned resource's own subjectCategories contradicts the index."""

    capability: NetworkSchemaType
    observed_category: str


type DiscoveryEvent = (
    CapabilityUnresolved
    | ExpiredAnswerDropped
    | AskDiscoveryFailed
    | AskUnservable
    | CategoryMappingDiverged
)


@dataclass(frozen=True)
class DiscoveryResult:
    answers: dict[int, tuple[DiscoveredAnswer, ...]]
    capabilities: dict[int, tuple[ProviderCapability, ...]]
    failures: dict[int, tuple[DiscoveryFailure, ...]]
    events: tuple[DiscoveryEvent, ...]


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
    flattened_fields: dict[str, FieldSpec] = dataclass_field(default_factory=dict)
