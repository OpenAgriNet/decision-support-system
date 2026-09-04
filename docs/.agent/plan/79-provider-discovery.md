# Provider Discovery

Plan for issue #79. Component 4 of `docs/dss-design-v2.md` §6.

---

## 1. The problem

A farmer asks a question. Intent turns it into asks. Before anything can be
planned, the DSS has to know **who can answer each ask**.

That knowledge lives on the network, not in the DSS. Provider Discovery is the
component that goes and gets it — and only that. It does not call providers, and
it does not plan.

Nothing in the repo does this yet. `src/dss/core/` holds `intent/`, `moderation/`
and `shared/`, all docstring-only. `ports/` is empty. This change builds the
first slice that talks to the network.

---

## 2. What the network actually gives us

Two hops, both **synchronous** — `on_discover` is the 200 response body to
`POST /discover`, not a callback. Same for `on_select`.

**`/discover` → `on_discover`.** Answered from the published catalog. Returns
catalogs, each naming its own provider, each holding resources.

**`/select` → `on_select`.** Consumer node straight to the provider node. The
provider node resolves its binding on `"<provider>|<@type>"`, calls the upstream
(IMD, Agmarknet), maps the response, and returns values.

### The `informationMode` split

Every resource declares `informationMode: OnDemand | Direct` — see
`dss-design-v2.md` §6.4 for what each mode means and why it matters.

The consequence that shapes this component: **discovery returns two different
kinds of thing.** An answer, or a lead to call. `Direct` costs nothing to use;
`OnDemand` costs a `select`, up to `timeoutMs × retryMax` (120s in the sample).

---

## 3. Decisions

### 3.1 The Network Adapter is a separate service

The DSS holds a client adapter that calls it over HTTP. The Network Adapter
service owns credentials, signing, protocol envelopes, and the provider-node hop.

Follows `docs/DSS_ARCHITECTURE.md` §1.2 — the DSS never signs, verifies,
correlates, or delivers network protocol messages.

**Rejected:** the DSS hosting a callback endpoint and owning correlation. The
contract is synchronous, so there is no callback to host — and §8.1 assigns that
work to the adapter regardless.

### 3.2 Two ports, not one

```python
# ports/discovery.py
class CapabilityDiscovery(Protocol):
    async def discover(self, query: ProviderQuery) -> DiscoveryResult: ...

# ports/invocation.py
class CapabilityInvocation(Protocol):
    async def select(self, capability: ProviderCapability, ...) -> DiscoveredAnswer: ...
```

The design doc's barrier rule (§3): everything up to `Plan` can be thrown away;
everything from `Plan Executioner` cannot, because a provider call may already
have happened. Discovery runs *alongside* moderation, so at the moment
`discover()` executes the turn is not yet cleared.

Two ports make that enforceable by type. A component holding only
`CapabilityDiscovery` **cannot** invoke a provider.

**Rejected:** one `NetworkConsumerAdapter` port with both methods. Anything
holding it could invoke a provider, and "read-only may cross the barrier" stays
a comment rather than a property.

This is a guardrail, not a wall — Python does not enforce it. If it needs to
hold the way ADR-0001's framework boundary holds, it needs the same treatment: a
test asserting no module in the discovery slice imports `CapabilityInvocation`.

### 3.3 Ports arrive as explicit parameters

Core functions take exactly the ports they use, as arguments. `orchestration/`
binds the concrete adapters.

```python
async def discover_providers(
    intent: Intent, turn: UserTurn, ctx: TurnContext,
    discovery: CapabilityDiscovery,
) -> DiscoveryResult: ...
```

**Rejected:** a container or registry the services pull from. It dissolves 3.2 —
anything with the container reaches everything — and tier-1 tests would set up a
container instead of passing a fake. In Python a function's parameters *are* its
dependency declaration.

### 3.4 The DSS sends a domain query; the adapter builds the protocol message

`core` produces `ProviderQuery` with domain fields only:

```python
@dataclass(frozen=True)
class ProviderQuery:
    capabilities: tuple[str, ...]   # @type values — one, or several when unpinned (3.6)
    languages: tuple[str, ...]      # from ctx.target_lang
    coverage: Coverage | None       # from turn.location; None when absent


@dataclass(frozen=True)
class Coverage:
    lat: float
    lon: float
    radius_m: int                   # TODO: config, or derived from Ask? undecided
```

`frozen=True` with only hashable fields makes it usable as a dict key, which is
what 3.13's dedupe needs. **No ask index, no trace id, no timestamp** — those
live in the envelope the adapter builds, and putting any of them here would
silently break dedupe.

`Coverage` is lat/lon/radius rather than a GeoJSON geometry so that
`[longitude, latitude]` ordering stays protocol knowledge. The design doc rounds
lat/lon to ~1km (Open #8), so `radius_m` finer than that is meaningless.

The client adapter turns this into the `/discover` body: `context.schemaContext`
and the `filters` JSONPath (both from `capabilities` — belt and braces, since the
contract accepts either), the `S_DWITHIN` spatial constraint with
`[longitude, latitude]` and `srid: EPSG:4326`, and the envelope with
`messageId`, `transactionId`, `timestamp`, `action`, `version`.

**Rejected:** the DSS building the ONIX `intent` object. JSONPath and spatial
operators are protocol; §1.2 keeps them out of the DSS, and a protocol version
bump should rewrite only the adapter.

### 3.5 No LLM in discovery, and no `textSearch`

`textSearch` is not supported by the network yet, so it is omitted.

**Consequence worth stating plainly:** `textSearch` was the only signal that
ranks. Filters narrow, never rank. So discovery returns an **unordered** set and
all ordering falls to `provider_selector` config order. This is the design until
`textSearch` lands — not a gap to patch later. Related: design doc Open #10.

**Rejected:** an LLM call to fill filter values. No schema declares a required
filter — every `required:` in `network-specs` is gated on `informationMode` and
constrains the *publisher's* record, not the consumer's query. So every filter
is a narrowing *choice*, and a wrong guess **excludes** a provider that could
have answered. A false negative you cannot see.

### 3.6 `@type` is resolved from two axes, via the cached schema

The two-axis reading of `subjectCategories` — verticals (`Crop`, `Livestock`,
`Weather`, `Market`, `Scheme`) and horizontals (`Knowledge`, `Service`) — is
recorded in `dss-design-v2.md` §5 (Taxonomy) and §6.4. Not restated here.

For this component: the vertical comes from `Ask.category`, the horizontal from
`Ask.action_type`, and the pair resolves through the cached schema index to one
or more `@type` values for the query filter.

**One-to-many is deliberate.** Normally a pair resolves to one `@type`. When it
cannot be pinned — `Market` + `LOOKUP` plausibly hitting both `MandiPrice` and
`MarketIntelligence` — the query carries several rather than guessing. Forcing
one-to-one would make the mapping lie.

**Rejected:** a static config map. It needs a config edit per new pack. Instead
the pair resolves through the schema-pack index (3.6.1), rebuilt on refresh, so
a new pack is picked up without a DSS change.

**Known weakness.** No pack declares which categories it serves —
`subjectCategories` appears in exactly one `attributes.yaml` (the base) and no
pack narrows it. So the index is inferred from each pack's `examples/*.json`,
which makes it a **convention, not a contract**: a publisher emitting
`MandiPrice` with `["Scheme"]` would validate.

The fix belongs upstream: one `subject_categories` key per `profile.json` in
`network-specs`. Worth raising with them — the derivation code then reads a
declaration instead of examples, with no shape change in the DSS.

**A pair that resolves to zero `@type` values is a defect, not an unservable
ask.** It means the enum gained a value the index does not cover, or the index
has a hole (Assumption 1) — our side, and every turn for that category, which is
3.14's defect signature exactly. So: emit `CapabilityUnresolved(vertical,
horizontal)` as a defect-class event, issue no query, and leave the ask with
empty collections. The farmer gets `no_match`, which is correct; the operator
gets a loud signal.

Treating it as an ordinary unservable ask is what this avoids — an index gap
would then look identical to "nobody on the network does this", the exact
conflation 3.14 exists to prevent.

#### 3.6.1 The schema-pack cache

The index is built from a local copy of the `network-specs` packs.

**Bundled at build, refreshed periodically, bundle as the permanent floor.** A
copy ships in the image so a cold start needs no network. Refresh happens on an
interval. If a refresh fails, the last good copy continues to serve — the design
doc says this for `DomainSchemas` already ("a stale cache serves the last good
copy rather than failing the turn"), and it means a `network-specs` outage cannot
empty the `@type` map.

**Read `main` only.** Not `schema-packs-v0.1` or any other branch.

**Pin the commit.** `network-specs` is untagged and `Status: Proposed for
review`. Tracking a moving ref means the `@type` map can change without a deploy,
which makes refresh an uncontrolled input. Pin, and bump deliberately.

**Consume three files per pack, not all six:**

| File | Why |
|---|---|
| `profile.json` | `filterable_paths`, and `subject_categories` when it lands |
| `attributes.yaml` | the `@type` `const` — the only exact pack discriminator |
| `examples/*.json` | the inferred category index, until `profile.json` declares it |

`vocab.jsonld` is an RDFS dictionary, `renderer.json` is presentation, and
`context.jsonld` is only needed if the DSS resolves JSON-LD — which it does not.
Skipping them keeps the parse surface small.

**Behind a replaceable seam.** Fetch and parse sit behind a port so a hosted
schema registry can replace the checkout without touching the index or the
resolver. Worth knowing the two change independently: a registry changes *fetch*;
a registry that serves pre-indexed categories changes *parse*. One port with both
methods is enough now.

### 3.7 Reconciliation is diagnostic, not corrective

Returned resources carry their own `subjectCategories`. When an observed category
contradicts the index, emit `CategoryMappingDiverged` and change nothing.

Because the query filters on `@type`, reconciliation can *confirm* a mapping and
*discover additional categories* for a pack already queried. It cannot discover a
pack never queried — that gap is closed by schema refresh, not by observation.
The two mechanisms are complementary.

**Rejected:** auto-widening the map from observations. It silently papers over a
publisher's mistake and makes DSS behaviour drift from anything written down.
**Rejected:** rebuilding the map from a rolling window. Discovery behaviour would
depend on recent traffic — hard to reproduce in a test, hard to explain in an
incident.

If divergences turn out frequent and benign, auto-widening becomes the obvious
follow-up *and there will be data to justify it*. Starting there means never
collecting that data.

### 3.8 No subject filtering in discovery

`Ask.subject` is the farmer's own word — `"potato"`, and nothing resolves it to a
governed id (design doc Open #7). Matching it against `descriptor.code` by string
equality fails the real cases: "rice" vs "paddy", `"आलू"` vs `"potato"`.

Semantic matching would fix that but needs embeddings or an LLM — and the design
doc explicitly rejected embeddings for the near-identical Tool Discovery job
("Keyword search, not embeddings. BM25. No vector store, no embedding model").
BM25 would not help, since it solves case and stemming, not synonymy.

So discovery does not filter by subject at all. The planner judges relevance,
where there is already an LLM and real candidate data.

**Prerequisite for the deterministic version:** a subject glossary, so "rice"
maps to "paddy". Design doc Open #7.

**Cost accepted:** a larger candidate set into the planner prompt, and more
provider calls. Trading tokens and latency for correctness.

### 3.9 Discovery returns two kinds of result, plus failures

```python
class DiscoveryResult:
    answers: dict[int, tuple[DiscoveredAnswer, ...]]        # Direct — values present
    capabilities: dict[int, tuple[ProviderCapability, ...]] # OnDemand — must select
    failures: dict[int, tuple[DiscoveryFailure, ...]]       # by ask index
    events: tuple[DiscoveryEvent, ...]                      # side-channel, see 3.15
```

The first three are results the planner reads. `events` is a **side-channel for
the sinks** — `orchestration/` consumes it and the planner never looks at it. It
rides on the same return value rather than a separate tuple so callers do not
unpack two things to reach one; the design doc's `Evidence` mixes results with
`failed` the same way.

Two result types, not one with a mode flag, because they have different truth
conditions and different failure modes. A `Direct` resource can be stale but
cannot fail. An `OnDemand` resource cannot be stale but its `select` can fail —
and can cost 120s of retries first.

**An empty result is not a failure.** `BIZ_NO_RESULTS_FOUND` — nothing in the
catalog matched — leaves all three collections empty for that ask and adds **no**
`DiscoveryFailure`. It is a legitimate answer, and the turn ends `no_match` per
`docs/api-contracts/api-contract.md`. A failure entry is reserved for the cases
in 3.14, which surface as `error` + `provider_unavailable` instead. The
api-contract keeps those two statuses distinct, so discovery must not blur them.

**Rejected:** one type with `information_mode` and an optional payload. Every
consumer would check the flag, and forgetting the check is a live bug — reading
`supportedParameters` as if it were an answer.

**Rejected:** normalising the difference away by having the adapter serve `Direct`
from the catalog. It hides the latency difference, which sufficiency and fallback
logic need to see.

`failures` mirrors `Evidence.failed` in the design doc — failures as data, not
exceptions.

### 3.10 `ProviderCapability`, not `Provider`

The design doc's `Provider` has a single `capability: str`, which does not fit a
catalog: one provider publishes many resources, each with its own `@type`.

The DSS models **what it intends to use**, not the provider's full inventory. The
unit is a capability offered by a named provider:

```python
class ProviderCapability:
    provider_id: str
    provider_name: str
    capability: str          # the resource's @type
    resource_id: str         # names which resource `select` commits to
    offer_id: str            # TODO: rename — ecommerce vocabulary, needs an OAN term
    kind: ProviderKind
```

`resource_id` and `offer_id` are both load-bearing: `select` sends
`contract.commitments[].resources[].id` and `.offer.id`, and the provider node
resolves its binding on `"<provider_id>|<capability>"`.

`Provider` stays what `docs/DSS_ARCHITECTURE.md` means by it — a network
participant. Calling a provider-capability pair a `Provider` overloads a term
that already means something.

**Rejected:** `Provider` with `capabilities: tuple[str, ...]`. `by_ask` becomes
ambiguous — a returned provider carries capabilities unrelated to the ask — so
consumers must re-filter, and every selection strategy needs the matched
capability threaded alongside. That rebuilds the flat pair with the provider
object duplicated. Provider-level affinity is recoverable from the flat form by
grouping on `provider_id`.

### 3.11 `kind` is derived from the host URL, in the DSS client adapter

Every network has a discovery service, and discovery services federate — so one
response can carry catalogs from more than one network. The client adapter knows
its own configured discovery host and compares.

**To verify:** the sampled `on_discover` carries `senderUri`/`receiverUri` on the
*context*, which describes the hop, not each catalog. If a federated response
does not attribute each catalog individually, `kind` can only mean "this response
came from a remote hop" — coarser than per-provider. Check against a real
federated response.

### 3.12 Expired `Direct` answers fall back visibly

`network-specs` is explicit: *"The mode does not indicate freshness; timestamps
and validity express when the information applies."* So a `Direct` resource can
be outside its `validity` window.

The DSS drops it, and when an `OnDemand` capability exists on the same
`provider_id` + `capability`, records the substitution explicitly.

Why visible rather than silent: the DSS is the only place that sees both the
expired answer and the fallback. Without the link, a cache-miss `select` is
indistinguishable from a normal one — and `select` can cost 120s, so it matters
how many are caused by discovery-service staleness.

**Not cache management.** `DSS_ARCHITECTURE.md` §5.4 keeps catalog caching
outside the DSS. Reading `validity` is reading the provider's own statement of
when the information applies, which is closer to §4.2's runtime catalog
validation. The failure this prevents: a farmer gets yesterday's forecast
presented as today's.

When a provider publishes *only* an expired `Direct` resource, the ask has no
candidate — the design doc's normal no-match path. Correct behaviour, not a gap.

### 3.13 One query per distinct query value, mapped back to every ask

Fan out per ask, then dedupe by query value. With no `textSearch` and no subject
filter, two asks in the same vertical produce **byte-identical** queries — the
normal case for a multi-ask turn, not an edge case.

`ProviderQuery` must therefore be a value type with no turn-specific fields; ids
and timestamps live in the envelope the adapter builds. Query equality is then
honest equality.

When subject support reaches the network adapter, subjects enter `ProviderQuery`,
queries stop matching, and this degrades to one call per ask with no rework.

**Rejected:** one batched call covering every ask. It couples the asks — one
malformed filter fails all of them, a `429` loses all of them. Per-ask queries
degrade independently.

### 3.14 Failures are classified; partial answers are served

| Class | Codes | Behaviour |
|---|---|---|
| Transient | `429`, `500`, `NET_*` | degrade — this ask gets no candidates |
| Defect | `400`, `401`, `403` | degrade, but loud — this is our bug |

A `400` means the DSS composed an invalid query, or credentials are wrong. That
is a defect, and it is not a per-turn anomaly — it is *every* turn for that
capability. Silent degradation would ship a broken mapping and show only
slightly worse answers.

Loudness comes from three places: the failure carries its class; it goes to the
`TurnSink` (required, always present) not just optional telemetry; and its
every-turn signature makes it alertable.

**The distinction that must survive to the composer:** "no provider serves onion
prices" and "we could not reach discovery" both produce zero candidates, but they
are different statements. The design doc is firm that `sufficient=False` means "I
don't know" — telling a farmer no provider exists when the service was
rate-limited is inventing. This is why `failures` is on `DiscoveryResult` and not
only in a sink: the composer reads the plan, not the sink.

**Rejected:** failing the whole turn on any failure. One flaky capability could
take down every turn that mentions it.

### 3.15 Telemetry is returned as data, written in `orchestration/`

`core` returns typed events alongside its results. `orchestration/` routes them
to the sinks and is the single point where redaction happens.

This keeps 3.2 intact — writing to a sink is a side effect, and a read-only
discovery service must not have one. It also matters for the barrier: discovery
runs before moderation clears, so emitting mid-fan-out would write telemetry
about a turn that may be about to be rejected.

Routing, per the design doc's two sinks (§3):

| Event | `TurnSink` | `TelemetrySink` | Emitted when |
|---|---|---|---|
| `ExpiredAnswerDropped` | ✓ explains this farmer's answer | ✓ staleness rate | a `Direct` answer is outside `validity` (3.12) |
| `AskDiscoveryFailed` | ✓ explains a partial answer | ✓ error rate | transient or defect failure (3.14) |
| `AskUnservable` | ✓ | ✓ | an ask ends with no answer and no capability |
| `CapabilityUnresolved` | ✓ | ✓ | the axis pair resolves to zero `@type` — defect (3.6) |
| `CategoryMappingDiverged` | — | ✓ | observed category outside the index (3.7) |

`AskUnservable` covers the empty-catalog case and is the one event that is *not*
a problem on our side — it is the normal `no_match` path. It still goes to the
`TurnSink` because it explains why this farmer got fewer answers than asks.

Events are one frozen dataclass per event type, living with the slice that emits
them — the same grain as every other component type in the design doc. Typed
rather than `dict[str, Any]` because redaction at a single write point only works
if the sink can tell which fields carry PII; an untyped payload degrades
redaction to key-name pattern matching.

None of these four events carries farmer content — provider ids, capability
strings, ask indices, status codes. Worth keeping true as events are added.

---

## 4. Scope

The slice lives in `core/provider_discovery/`. The design doc calls the component
"Provider Discovery" (§3 diagram, §6.4), and Tool Discovery arrives later as
`core/tool_discovery/`, so the pair reads consistently. `CLAUDE.md` lists
`routing` for this slice — that name predates the design doc and
`DSS_ARCHITECTURE.md` §8.3 still has "Router scope" open, so it is not used here;
update `CLAUDE.md`'s folder list when the directory lands.

**In:**
- `ports/discovery.py`, `ports/invocation.py`, `ports/schema_packs.py`
- `core/provider_discovery/models.py`, `service.py` — `discover_providers`, pure
- the DSS client adapter under `adapters/`
- the schema-pack cache, index and resolver (3.6.1)
- `orchestration/` wiring and sink routing

**Out:**
- **`select` / `on_select`.** The port is declared; the implementation belongs
  with the Plan Executioner.
- **`ProviderKind.LOCAL`** as a distinct source. `kind` is derived, but no local
  registry exists yet.
- **Subject filtering** — 3.8.
- **Ranking.** Nothing ranks until `textSearch` exists — 3.5, Open #10.
- **`textSearch`, `mediaSearch`** — not supported by the network.
- **Which candidate to call.** The planner's job. Open #10, #13.

**Defer first if the slice needs shrinking:** reconciliation (3.7). It is the one
item here that can be removed without making the component incorrect — it emits a
diagnostic and changes no behaviour. Everything else is load-bearing.

---

## 5. Verification

Tier 1 (`tests/unit/core/provider_discovery/`) must outnumber the rest combined.
Ports mocked, plain Python in and out.

- vertical + horizontal → `@type`, including the one-to-many case
- a pair resolving to zero `@type` → no query, `CapabilityUnresolved`, empty
  collections, and **no** `DiscoveryFailure`
- an empty catalog result → all collections empty, `AskUnservable`, and **no**
  `DiscoveryFailure` (distinct from the 3.14 failures)
- `Direct` inside validity → `answers`; outside → dropped, `ExpiredAnswerDropped`
- expired `Direct` with a sibling `OnDemand` → fallback recorded
- expired `Direct` with no sibling → ask unservable
- transient vs defect classification per status code
- one ask failing leaves other asks' results intact
- two asks with the same pair → one query, both result entries populated
- `ProviderQuery` equality and hashability hold for two asks sharing a pair
- `CategoryMappingDiverged` when an observed category is outside the index
- returned events are exactly as expected (asserting on values, not a mock's
  call log)

Schema-pack cache and index (3.6.1) — also tier 1, ports mocked:
- three pack files parse into an index; the other three are ignored
- a failed refresh keeps the last good index rather than emptying it
- a new pack in the fixture appears in the index without a code change

Tier 2 (`tests/integration/adapters/`) against a recorded `on_discover` fixture:
- catalog → `ProviderCapability` and `DiscoveredAnswer`, `resource_id` and
  `offer_id` preserved
- `kind` from host URL
- `ProviderQuery` → `schemaContext`, JSONPath filter, `S_DWITHIN` with
  `[longitude, latitude]` and `srid`
- no assertions on business logic

Tier 3 (`tests/integration/orchestration/`): ports mocked below, real wiring —
events reach the right sinks.

**Boundary test:** no module in the discovery slice imports
`CapabilityInvocation` (3.2).

---

## 6. Assumptions

1. **A new schema pack must extend the `subjectCategories` enum.** If it lands
   without doing so, the axes cannot place it and the index has a hole. The DSS
   cannot enforce this.
2. **The category index is inferred from `examples/*.json`** until `network-specs`
   declares it — 3.6.
3. **Two axes is the current shape of `subjectCategories`, and may change.** The
   direction holds even if the encoding does.
4. **A subject glossary is a prerequisite** for deterministic subject matching —
   3.8, Open #7.
5. **`offer_id` needs an OAN term.** Ecommerce vocabulary, kept for now.
6. **Per-catalog provenance in a federated response is unverified** — 3.11.
7. **`Coverage.radius_m` has no source yet** — 3.4. Samples use both 25km and
   250km. Config is the assumption until decided.
8. **How `network-specs` gets checked out onto disk is not yet built.**
   `FilesystemSchemaPackSource` assumes a pinned-commit checkout already
   exists at a configured path. Cloning/pulling that checkout, and a way to
   trigger a re-checkout + `SchemaPackCache.refresh()` together, is real
   remaining work — bigger than this slice, and depends on the entrypoint
   shape (still undecided).

---

## 7. Related decisions elsewhere

**Already applied to `dss-design-v2.md`** (commit `dec649a`) — `Provider` renamed
to `ProviderCapability`, `ProviderCandidates` replaced by `DiscoveryResult`,
Taxonomy corrected to seven categories with the two-axes reading, Open #3
corrected, Open #10 sharpened, Open #15 added. This plan does not restate them.

**Still to raise with `network-specs`:** a `subject_categories` key on each
`profile.json`, so the category index reads a declaration instead of inferring
from examples (3.6).

**Depends on `docs/ADR/0002-dss-api-contract.md`** for the terminal statuses this
component's outcomes map to — `no_match` for an empty result, `error` +
`provider_unavailable` for a discovery failure (3.9, 3.14).
