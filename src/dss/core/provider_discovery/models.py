"""Types produced and consumed by provider_discovery.

A type answers questions about its own data; service.py runs the flow.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from datetime import datetime
from enum import Enum

from dss.core.provider_preferences.models import AskPriority

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
    """The (subject_category, action_type) pair resolves to zero @type values.

    Every valid pair should map to at least one @type, so this means our own
    mapping is incomplete — not that no provider serves this pair.
    """

    subject_category: str
    action_type: str


@dataclass(frozen=True)
class AsksToBeDiscovered:
    queries_by_ask: Mapping[int, ProviderQuery]  # only asks that can be searched
    unsearchable_asks: frozenset[int]  # a failed place, or no @type for the ask
    events: tuple[CapabilityUnresolved, ...]

    def find_unique_queries(self) -> tuple[ProviderQuery, ...]:
        """Each query once, in the order of the first ask that needs it."""

        return tuple(dict.fromkeys(query for _, query in self._in_ask_order()))

    def find_asks_sharing(self, query: ProviderQuery) -> tuple[int, ...]:
        """In ask order."""

        return tuple(index for index, shared in self._in_ask_order() if shared == query)

    def _in_ask_order(self) -> list[tuple[int, ProviderQuery]]:
        return sorted(self.queries_by_ask.items(), key=lambda item: item[0])


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
    # What the calling client's provider ranking did to each ask, keyed by ask
    # index (ADR-0019). Filled by `core.provider_preferences.prioritise`, which
    # runs over this result before anything downstream reads it.
    #
    # Here rather than alongside, because the ordering above *is* the thing it
    # explains: `capabilities[2]` being one provider long is either "only one
    # provider serves this" or "the client ranked the other two out", and
    # nothing else can tell those apart. Defaulted, so every existing
    # construction — and an unwired deployment — stays valid.
    priorities: dict[int, AskPriority] = dataclass_field(default_factory=dict)

    def serves(self, position: int) -> bool:
        """Whether anyone was found for the ask at ``position``: a Direct
        answer in ``answers`` or an OnDemand capability in ``capabilities``."""

        return bool(self.answers.get(position) or self.capabilities.get(position))

    @property
    def serves_any_ask(self) -> bool:
        return any(self.answers.values()) or any(self.capabilities.values())


_NOTHING_FOUND = ProviderQueryResult(answers=(), capabilities=(), failures=())


@dataclass(frozen=True)
class DiscoveredAsk:
    index: int
    query: ProviderQuery
    query_result: ProviderQueryResult

    def within_validity(self, now: datetime) -> DiscoveredAsk:
        fresh = tuple(
            answer
            for answer in self.query_result.answers
            if not answer.is_outside_validity(now)
        )
        return replace(self, query_result=replace(self.query_result, answers=fresh))

    def build_expired_answer_events(
        self, now: datetime
    ) -> tuple[ExpiredAnswerDropped, ...]:
        return tuple(
            ExpiredAnswerDropped(
                provider_id=answer.provider_id,
                capability=answer.capability,
                resource_id=answer.resource_id,
                had_fallback=self.query_result.has_on_demand_fallback_for(answer),
            )
            for answer in self.query_result.answers
            if answer.is_outside_validity(now)
        )

    def build_outcome_events(self) -> tuple[AskDiscoveryFailed | AskUnservable, ...]:
        if self.query_result.failures:
            return tuple(
                AskDiscoveryFailed(
                    ask_index=self.index,
                    capability=failure.capability,
                    failure_class=failure.failure_class,
                    status_code=failure.status_code,
                )
                for failure in self.query_result.failures
            )
        if not self.query_result.answers and not self.query_result.capabilities:
            return (
                AskUnservable(
                    ask_index=self.index, capabilities=self.query.capabilities
                ),
            )
        return ()


@dataclass(frozen=True)
class DiscoveredAsks:
    asks: tuple[DiscoveredAsk, ...]  # the searched asks, in ask order
    unsearchable_asks: frozenset[int]  # never searched

    def within_validity(self, now: datetime) -> DiscoveredAsks:
        return replace(self, asks=tuple(ask.within_validity(now) for ask in self.asks))

    def build_expired_answer_events(
        self, now: datetime
    ) -> tuple[ExpiredAnswerDropped, ...]:
        return tuple(
            event for ask in self.asks for event in ask.build_expired_answer_events(now)
        )

    def build_outcome_events(self) -> tuple[AskDiscoveryFailed | AskUnservable, ...]:
        return tuple(event for ask in self.asks for event in ask.build_outcome_events())

    def build_result(self, events: Sequence[DiscoveryEvent]) -> DiscoveryResult:
        """An entry for every ask, searched or not, in ask order."""

        results = self._find_query_results_by_ask()
        return DiscoveryResult(
            answers={index: result.answers for index, result in results.items()},
            capabilities={
                index: result.capabilities for index, result in results.items()
            },
            failures={index: result.failures for index, result in results.items()},
            events=tuple(events),
        )

    def _find_query_results_by_ask(self) -> dict[int, ProviderQueryResult]:
        searched = {ask.index: ask.query_result for ask in self.asks}
        never_searched = dict.fromkeys(self.unsearchable_asks, _NOTHING_FOUND)
        return dict(sorted((searched | never_searched).items()))
