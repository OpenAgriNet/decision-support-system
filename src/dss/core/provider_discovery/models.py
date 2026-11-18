"""A type answers questions about its own data. service.py runs the flow."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from datetime import datetime
from enum import Enum

from dss.core.intent.models import Ask, AskId
from dss.core.provider_preferences.models import AskPriority

# A resource's `@type`, like `openagrinet:MandiPrice`. Not an enum: the set is
# open, fed by the schema packs a deployment mounts. Most call sites call it
# `capability`.
NetworkSchemaType = str

# The pack types for each (subject category, action type) pair.
type CapabilityIndex = Mapping[tuple[str, str], tuple[NetworkSchemaType, ...]]


@dataclass(frozen=True)
class Coverage:
    lat: float
    lon: float
    radius_m: int


@dataclass(frozen=True)
class ProviderQuery:
    """No ask id, trace id or timestamp: those belong to the envelope the
    client adapter builds."""

    capabilities: tuple[str, ...]
    # What the JSONPath filter matches on ("Weather"). `capabilities` is not
    # in the filter: it names the envelope's `schemaContext`, which already
    # pins the resource type.
    subject_category: str
    languages: tuple[str, ...]
    coverage: Coverage | None


@dataclass(frozen=True)
class ProviderCapability:
    """What discovery intends to use, not the provider's full inventory.
    ``kind`` waits on host-URL derivation."""

    provider_id: str
    provider_name: str
    capability: NetworkSchemaType
    resource_id: str
    bpp_id: str
    bpp_uri: str
    observed_categories: tuple[str, ...] = ()  # the resource's own subjectCategories
    # From on_discover ("AGMARKNET-01"). Kept, not sent: select names a
    # provider by id and name. Optional: only `name` is in every fixture.
    provider_code: str | None = None
    # What the resource says it supports, as sent ("supportedCommodities": [...]).
    # An opaque map: the fields differ per pack, and every pack shares this type.
    advertised: Mapping[str, object] = dataclass_field(default_factory=dict)


@dataclass(frozen=True)
class Validity:
    starts_at: datetime | None
    ends_at: datetime | None


@dataclass(frozen=True)
class DiscoveredAnswer:
    """A Direct resource's answer. ``attributes`` is the pack's
    ``resourceAttributes`` as sent: discovery does not know each pack's schema."""

    provider_id: str
    provider_name: str
    capability: NetworkSchemaType
    resource_id: str
    attributes: dict[str, object]
    validity: Validity | None
    # Who made the data, when that is not who we called. E.g. a provider that
    # passes on IMD's forecast gives IMD as the source.
    source_id: str | None = None
    source_name: str | None = None
    source_url: str | None = None

    def is_outside_validity(self, now: datetime) -> bool:
        """Both bounds: an answer not yet valid is as wrong as an expired one."""

        if self.validity is None:
            return False
        if self.validity.starts_at is not None and now < self.validity.starts_at:
            return True
        return self.validity.ends_at is not None and now > self.validity.ends_at


@dataclass(frozen=True)
class ProviderQueryResult:
    answers: tuple[DiscoveredAnswer, ...]
    capabilities: tuple[ProviderCapability, ...]
    failures: tuple[DiscoveryFailure, ...]

    def has_on_demand_fallback_for(self, answer: DiscoveredAnswer) -> bool:
        return any(
            capability.provider_id == answer.provider_id
            and capability.capability == answer.capability
            for capability in self.capabilities
        )


@dataclass(frozen=True)
class CapabilityUnresolved:
    """The (subject category, action type) pair maps to no @type. Our own
    mapping is incomplete: it does not mean no provider serves the pair."""

    subject_category: str
    action_type: str


@dataclass(frozen=True)
class AskToDiscover:
    ask: Ask
    query: ProviderQuery | None  # None: a failed place, or no @type for the ask


@dataclass(frozen=True)
class AsksToBeDiscovered:
    asks: tuple[AskToDiscover, ...]
    events: tuple[CapabilityUnresolved, ...]

    def find_unique_queries(self) -> tuple[ProviderQuery, ...]:
        """Each query once, in the order of the first ask that needs it."""

        return tuple(
            dict.fromkeys(ask.query for ask in self.asks if ask.query is not None)
        )


class FailureClass(Enum):
    TRANSIENT = "transient"
    DEFECT = "defect"


@dataclass(frozen=True)
class DiscoveryFailure:
    """``provider_id`` is None when the whole call failed. It is set when one
    catalog of a good response was dropped (no BPP to address). The ask then
    counts as failed in the discovery span, even if another catalog serves it."""

    capability: NetworkSchemaType
    status_code: int
    failure_class: FailureClass
    provider_id: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class ExpiredAnswerDropped:
    """Also raised for an answer that is not valid yet. Not renamed: the
    sinks consume the event type."""

    provider_id: str
    capability: NetworkSchemaType
    resource_id: str
    had_fallback: bool  # an OnDemand capability existed on the same provider+capability


@dataclass(frozen=True)
class AskDiscoveryFailed:
    ask_id: AskId
    capability: NetworkSchemaType
    failure_class: FailureClass
    status_code: int


@dataclass(frozen=True)
class AskUnservable:
    """Nobody serves the ask. Not an error: the normal no-match path."""

    ask_id: AskId
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


NOTHING_FOUND = ProviderQueryResult(answers=(), capabilities=(), failures=())


@dataclass(frozen=True)
class DiscoveredAsk:
    ask: Ask
    # Asks that searched the same thing hold the same query. None: never searched.
    query: ProviderQuery | None
    found: ProviderQueryResult
    priority: AskPriority | None = None  # None: the ranking step did not run

    @property
    def answers(self) -> tuple[DiscoveredAnswer, ...]:
        return self.found.answers

    @property
    def capabilities(self) -> tuple[ProviderCapability, ...]:
        return self.found.capabilities

    @property
    def failures(self) -> tuple[DiscoveryFailure, ...]:
        return self.found.failures

    @property
    def is_served(self) -> bool:
        return bool(self.answers or self.capabilities)

    @property
    def is_ranked(self) -> bool:
        return self.priority is not None and self.priority.applied

    def find_capability(self, resource_id: str) -> ProviderCapability | None:
        return next(
            (
                capability
                for capability in self.capabilities
                if capability.resource_id == resource_id
            ),
            None,
        )

    def find_capabilities_of_type(
        self, schema_type: NetworkSchemaType
    ) -> tuple[ProviderCapability, ...]:
        return tuple(
            capability
            for capability in self.capabilities
            if capability.capability == schema_type
        )

    def with_capabilities(
        self, capabilities: tuple[ProviderCapability, ...]
    ) -> DiscoveredAsk:
        return replace(self, found=replace(self.found, capabilities=capabilities))

    def ranked(
        self,
        priority: AskPriority,
        *,
        capabilities: tuple[ProviderCapability, ...],
        answers: tuple[DiscoveredAnswer, ...],
    ) -> DiscoveredAsk:
        found = replace(self.found, capabilities=capabilities, answers=answers)
        return replace(self, found=found, priority=priority)

    def within_validity(self, now: datetime) -> DiscoveredAsk:
        fresh = tuple(
            answer for answer in self.answers if not answer.is_outside_validity(now)
        )
        return replace(self, found=replace(self.found, answers=fresh))

    def build_expired_answer_events(
        self, now: datetime
    ) -> tuple[ExpiredAnswerDropped, ...]:
        return tuple(
            ExpiredAnswerDropped(
                provider_id=answer.provider_id,
                capability=answer.capability,
                resource_id=answer.resource_id,
                had_fallback=self.found.has_on_demand_fallback_for(answer),
            )
            for answer in self.answers
            if answer.is_outside_validity(now)
        )

    def build_outcome_events(self) -> tuple[AskDiscoveryFailed | AskUnservable, ...]:
        if self.query is None:
            return ()
        if self.failures:
            return tuple(
                AskDiscoveryFailed(
                    ask_id=self.ask.id,
                    capability=failure.capability,
                    failure_class=failure.failure_class,
                    status_code=failure.status_code,
                )
                for failure in self.failures
            )
        if not self.is_served:
            return (
                AskUnservable(ask_id=self.ask.id, capabilities=self.query.capabilities),
            )
        return ()


@dataclass(frozen=True)
class DiscoveryResult:
    asks: tuple[DiscoveredAsk, ...]
    events: tuple[DiscoveryEvent, ...]

    def __post_init__(self) -> None:
        seen: set[AskId] = set()
        for discovered in self.asks:
            if discovered.ask.id in seen:
                raise ValueError(f"two entries for ask {discovered.ask.id}")
            seen.add(discovered.ask.id)

    @property
    def serves_any_ask(self) -> bool:
        return any(ask.is_served for ask in self.asks)

    def find(self, ask_id: AskId) -> DiscoveredAsk | None:
        return next((ask for ask in self.asks if ask.ask.id == ask_id), None)

    def with_each_ask(
        self, change: Callable[[DiscoveredAsk], DiscoveredAsk]
    ) -> DiscoveryResult:
        return replace(self, asks=tuple(change(ask) for ask in self.asks))

    # TODO(#249): the four dicts and `serves` below are views for readers not
    # moved to `asks` yet. They go with the last of those readers.

    @property
    def answers(self) -> dict[AskId, tuple[DiscoveredAnswer, ...]]:
        return {ask.ask.id: ask.answers for ask in self.asks}

    @property
    def capabilities(self) -> dict[AskId, tuple[ProviderCapability, ...]]:
        return {ask.ask.id: ask.capabilities for ask in self.asks}

    @property
    def failures(self) -> dict[AskId, tuple[DiscoveryFailure, ...]]:
        return {ask.ask.id: ask.failures for ask in self.asks}

    @property
    def priorities(self) -> dict[AskId, AskPriority]:
        return {
            ask.ask.id: ask.priority for ask in self.asks if ask.priority is not None
        }

    def serves(self, ask_id: AskId) -> bool:
        return any(ask.is_served for ask in self.asks if ask.ask.id == ask_id)
