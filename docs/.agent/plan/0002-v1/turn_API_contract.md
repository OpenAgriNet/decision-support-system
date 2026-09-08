# `POST /v1/turns` — the structure as built

**Issue:** #87 · **Status:** merged with `main`, ready for review · **Updated:** 2026-09-07
**Contract:** `docs/api-contracts/openapi.yaml` (authoritative for the wire)
**Decisions:** ADR-0001 framework · ADR-0002 contract · ADR-0003 parallel intent/moderation · ADR-0004 envelope · ADR-0005 anyio · ADR-0006 FastAPI

`main` merged provider discovery, which brought **real moderation and intent**;
this branch adopted them and dropped the stubs it carried. **376 tests, 99%
coverage, all three CI gates green.**

```bash
DSS_STUB_LLM=true uv run uvicorn --factory dss.entrypoint.app:create_app --port 8077
```

---

## 1. How a turn moves through it

```
   POST /v1/turns
        │
   ┌────▼─────────────────────────────────────────────┐
   │ adapters/http/v1/router.py                        │  OUTSIDE
   │   capacity -> readiness -> Content-Type -> body   │
   │   -> gunzip+cap -> parse -> Accept                │
   └────┬─────────────────────────────────────────────┘
        │  schema.TurnRequest
   ┌────▼─────────────────────┐
   │ adapters/http/v1/mapping │  wire -> domain, pure
   └────┬─────────────────────┘
        │  UserTurn, TurnContext
   ┌────▼─────────────────────┐
   │ ports/turn.py            │  INSIDE — the only thing the transport may call
   │   TurnRunner             │
   └────┬─────────────────────┘
        │
   ┌────▼─────────────────────────────────────────────┐
   │ orchestration/core_runner.py                      │  OUTSIDE
   │   owns the order, and nothing else                │
   └────┬─────────────────────────────────────────────┘
        │
   ┌────▼─────────────────────────────────────────────┐
   │ orchestration/turn.py :: run_turn                 │
   │   ┌─────────────────┐   ┌─────────────────────┐  │
   │   │ core/intent     │ ∥ │ core/moderation     │  │  parallel, ADR-0003
   │   │ classify_intent │   │ moderate + policies │  │
   │   └─────────────────┘   └─────────────────────┘  │
   │   not PROCEED -> Intent() blanked                 │
   └────┬─────────────────────────────────────────────┘
        │  TurnResult(intent, decision)
        │
        ├─ decision not PROCEED ──▶ messages_for(decision) ──▶ terminal, no claims
        │
   ┌────▼─────────────────────┐
   │ core/channel  compose()  │  INSIDE — STUB(#84)
   └────┬─────────────────────┘
        │  yields TurnStarted, Claim…, TurnFinished
   ┌────▼───────────────────────┐
   │ sse.py  or  router._single │  events -> frames, or drained to one body
   └────────────────────────────┘

   evidence, written by the runner at each step:
     ports/sinks.py  ->  adapters/sinks/file.py  ->  var/evidence/*.jsonl
```

Two arrows, opposite directions — that inversion is the pattern:

- **runtime:** transport → port → runner → core → port → adapter
- **imports:** adapter → port ← core. The port imports nothing.

**`core/` imports only** `__future__`, `enum`, `typing`, `re`, `datetime`,
`pydantic`, and itself. No HTTP client, no filesystem, no clock, no environment.

---

## 2. Every file, and what it holds

### Inside the hexagon

| File | Holds |
|---|---|
| `core/shared/models.py` | **inbound** (from `main`): `UserTurn` with `original_query`/`enriched_query`, `UserDetails`, `ReferenceToken`, `ConversationMessage`, `Geometry`, `Location`, BCP-47 validator · **outbound** (this branch): `TurnContext`, `TurnStatus`, `Cause`, `Source`, `SourceKind`, `TextBlock`, `RefusalBlock`, `TurnOutcome`, `TurnStarted`, `Claim`, `TurnFinished`, `TurnEvent`. A comment marks the seam |
| `core/shared/errors.py` | `DssError`, `ProviderUnavailable` — what adapters wrap vendor exceptions into |
| `core/moderation/` | `Outcome`, `ReasonCode`, `ModerationDecision`, `LlmModerationVerdict`; `moderate()`; `messages_for()` — the farmer-facing refusal text |
| `core/intent/` | `Intent` with `asks: tuple[Ask, ...]`, `SubjectCategory`, `InteractionType`; `classify_intent()` |
| `core/policy/models.py` | `Checkpoint`, `EvaluationKind`, `FailMode`, `WordCheckPolicy`, `LlmPolicy`, `PolicyPack` |
| `core/provider_discovery/` | `discover_providers()`, the capability index, the schema-pack cache |
| `core/channel/` | `ComposedAnswer`; `compose()`, `no_match_answer()` — **STUB(#84)** |
| `ports/turn.py` | `TurnRunner` — the driving port |
| `ports/sinks.py` | `TurnSink` (required), `TelemetrySink` (optional) |
| `ports/llm.py` | `LLMProvider.structured(system_prompt, user_query, schema)` |
| `ports/discovery.py` · `invocation.py` · `schema_packs.py` | `CapabilityDiscovery`, `CapabilityInvocation`, `SchemaPackSource` |

`TurnContext` carries `trace_id`, `message_id`, `session_id` — **not**
`transaction_id`, because `trace_id` already is it and `UserTurn` keeps its own
copy for the Provider hop.

### Outside

| File | Holds |
|---|---|
| `adapters/http/v1/schema.py` | the wire models. **camelCase lives here and nowhere else** — `_WireIn` (`populate_by_name=False`, so only camelCase is accepted) and `_WireOut` (built by field name, dumped `by_alias=True`). `Outcome` has an explicit serializer so required-and-nullable `cause` survives `exclude_none`. Plus `component_schemas()` for the OpenAPI document |
| `adapters/http/v1/mapping.py` | wire ↔ domain, pure. The clock and the minted id are arguments |
| `adapters/http/v1/sse.py` | `Stream` — owns event names and the sequence counter (1-based) |
| `adapters/http/v1/router.py` | the HTTP rules; admission ordered cheapest-rejection-first |
| `adapters/http/v1/problem.py` | 4xx/5xx bodies |
| `adapters/llm/pydantic_ai_provider.py` | the real `LLMProvider`, on Pydantic AI |
| `adapters/llm/stub.py` | `StubLLM` — **STUB(#83)**, canned answers keyed by schema, records every ask |
| `adapters/sinks/file.py` | `FileTurnSink`, `FileTelemetrySink` — JSON Lines. Drops `geometry` on write |
| `adapters/sinks/memory.py` · `stdout.py` | **STUB(#85)** and the stdout telemetry default |
| `adapters/discovery/` · `schema_packs/` | the Beckn discovery client and the filesystem pack source |
| `orchestration/turn.py` | `run_turn()` — intent ∥ moderation, gated |
| `orchestration/core_runner.py` | `CoreRunner` — the order, the event stream, the evidence writes, `Outcome` → `TurnStatus` |
| `orchestration/stub_runner.py` | **STUB(#81)** — a third `TurnRunner` for the port test. Nothing in production wires it |
| `orchestration/envelope.py` · `discovery.py` · `schema_packs.py` | `main`'s envelope mapping and the discovery composition |
| `entrypoint/app.py` | builds the `FastAPI` app, publishes the wire schemas into `components.schemas` |
| `entrypoint/composition.py` | **the only file that names concrete classes** |
| `config/settings.py` | pydantic-settings, `env_prefix="DSS_"` — model bindings, HTTP caps, evidence dir/url, `stub_llm` |
| `config/policy_loader.py` | loads the mounted YAML policy pack |

### Tests

| Path | Drives | Doubles |
|---|---|---|
| `tests/unit/adapters/http/v1/` | `mapping`, `sse`, `_wants_stream` | none — pure functions |
| `tests/unit/core/**` | the core services | scripted `LLMProvider` fakes |
| `tests/unit/entrypoint/` | `create_app`, `build_runner` | env via `monkeypatch` |
| `tests/integration/entrypoint/` | the HTTP layer | `FakeRunner` — no core |
| `tests/integration/orchestration/` | `run_turn`, `CoreRunner` | fake providers, `FakeTurnSink`, `FakeTelemetrySink` |
| `tests/integration/adapters/` | the sinks, the discovery client | `tmp_path`, `capsys`, `pytest-httpserver` |
| `tests/conformance/v1/` | camelCase both ways, OpenAPI refs, **responses against `openapi.yaml`** | `FakeRunner` |
| `tests/support/fakes.py` | `FakeRunner(events, fail_after=)`, `FakeTurnSink(fail_on=)`, `FakeTelemetrySink(fail=)` | hand-written, never `MagicMock` |

**Three boundary suites, each proven able to fail** by planting a violation:

| Suite | Rule |
|---|---|
| `test_framework_boundary.py` | `core/` imports no agent framework |
| `test_transport_boundary.py` | the transport may not call a core service — `entrypoint/composition.py` exempt by name, since naming every concrete type is its job |
| `test_core_isolation.py` | nothing inside imports outward, or anything impure (clock, filesystem, env, randomness, network) |

---

## 3. Decisions this structure records

| Decision | Where it shows up |
|---|---|
| One endpoint; `Accept` selects JSON or SSE | `router.py::_wants_stream` — no streaming flag anywhere |
| Streaming is the transport's business, not the runner's | the port returns an iterator; `sse.py` owns framing and the counter |
| camelCase on the wire, snake_case in Python | aliases on `_WireIn`/`_WireOut` only |
| Outcome is one axis; `unavailable` is the failure value | `TurnStatus`, six values, no `kind` |
| **Intent and moderation run in parallel** | `orchestration/turn.py` — ADR-0003 |
| **A moderation `Outcome` is not a turn status** — `CLARIFY` means `requires_input` | `core_runner.py::_STATUS_FOR` |
| **Refusal wording is written by core, not the runner** | `core/moderation/messages.py` |
| Citations per block | `TextBlock.source_ids` — **contract disagrees, §4** |
| No authentication | no auth middleware; `401`/`403` never emitted |
| Two evidence tiers with opposite failure rules | `ports/sinks.py`; telemetry swallowed, turn record propagates |
| `geometry` never written to a sink | `adapters/sinks/file.py::_turn` |
| The composition root is the process entry | `entrypoint/composition.py` — ADR-0006 |
| One provider per component | `composition.py` — ADR-0004; each may point at a different model |
| Sequential runner, no graph | `core_runner.py` — ADR-0001 makes `pydantic-graph` the escalation, not the start |

**Framework independence** is enforced (`core/` is clean, three suites prove it).
Framework *swappability* is designed for and now partly exercised — Pydantic AI
lives only in `adapters/llm/` and is swapped for `StubLLM` by one setting. Per
`10-planner-agent-poc.md` the eventual tool-calling loop will deliberately **not**
sit behind a port, so switching frameworks will mean rewriting
`orchestration/planner.py`: one module, `core/` and `ports/` untouched.

**One property lost in the merge.** `Point(lon, lat)` with named floats became
`main`'s `Geometry.coordinates: list[float]`, so a reversed pair is no longer
unrepresentable inside the hexagon. A range check cannot recover it either — for
Anand, `72.93` and `22.56` are both a valid latitude *and* longitude. The mapping
test is the only guard.

---

## 4. Contract conformance — seven closed, four open

`tests/conformance/v1/test_against_openapi.py` loads `openapi.yaml` and validates
rendered bodies with `jsonschema`. That closed the seven, and found two the
hand-written tests had missed.

**Closed:** `outcome.confidence` · `outcome.cause` as `null` rather than omitted ·
`message.error` · one `context.version` · `resMessageId` · `sequenceNumber` from
1 · `traceId` echoing the required `transactionId`.

The two it found: `envelopeVersion` was **illegal** under
`additionalProperties: false`, not merely misnamed; and response `messageId` is
required but was dropped whenever the caller omitted one — the transport now
mints it.

**Open:**

| Contract | Code | Blocked on |
|---|---|---|
| `content[].annotations` with `start_index`/`end_index` | `sourceIds[]` — an extra the spec permits, so responses still validate | **the unit of an offset** |
| `401 · 403 · 502 · 504` | not emitted; `406 · 413 · 415` added instead | **the auth posture** |
| `content[].type` is `text \| image` | `text` only | the attachment service contract |
| `tracestate` | not read | nothing — no behaviour is specified |

The reusable lesson: **`exclude_none=True` silently drops any null, so every
field the contract marks `required` and nullable is a latent bug of the same
shape.** Only validating against the spec catches that class.

---

## 5. What is stubbed

| Marker | Where | Behaviour | Replaced by |
|---|---|---|---|
| `STUB(#81)` | `orchestration/stub_runner.py` | canned events, reads the turn for nothing | nothing — test-only now |
| `STUB(#83)` | `adapters/llm/stub.py` | canned typed answers per schema | already built: `pydantic_ai_provider.py`, wired when `DSS_STUB_LLM` is unset |
| `STUB(#84)` | `core/channel/service.py` | fixed sentences and one source | composer + reviewer |
| `STUB(#85)` | `adapters/sinks/memory.py` | a dict | a durable store |
| `STUB(#86)` | `orchestration/core_runner.py` | a confidence number per status | intent and composition reporting their own |

**`DSS_STUB_LLM=true` half-moderates.** The canned provider reports no violation,
so `delete-command` and every other `llm`-evaluated policy lets the turn through;
deterministic policies (`profanity-filter`) still apply.

**A bug worth remembering:** the stub had no canned `LlmModerationVerdict`, so it
raised, moderation's `fail_mode=CLOSED` turned that into a refusal, and *every*
turn came back `moderation_unavailable`. **376 tests passed while the composed
application was broken** — every test injects its own fake, so only `create_app()`
takes that path, and the one test that did asserted the evidence files appeared,
which a refused turn also writes. It now asserts the outcome.

---

## 6. The flow this becomes

Half arrived with the merge:

```
intent ∥ moderation                ✅ run_turn, ADR-0003
discovery(intent.asks)             ⬜ on main; run_turn does not call it
plan(discovery, verdict, skills)   ⬜ the barrier — highest-value case left
sufficiency(evidence, asks)        ⬜ plain code, no LLM, ever
compose(evidence, identity)        ⬜ STUB(#84) today
```

Discovery is allowed to cross the moderation barrier — a discovery query is
read-only and disposable; a provider invocation is not. So the gate is **"no tool
call before the verdict resolves"**, not "moderation runs first".

New files that flow needs: `core/planner/{models,sufficiency}.py`,
`orchestration/planner.py`, `adapters/invocation/`.

The barrier is testable with stubs: a verdict that records when it was awaited,
an invocation stub that records when it was called, assert the ordering — then
reorder the runner and watch it fail.

---

## 7. Order

1. Wire `discover_providers` into the turn — it exists on `main`, unused.
2. `orchestration/planner.py` behind the barrier, with the barrier test first.
3. `core/planner/sufficiency.py` — real code, tier 1.
4. Composer reads `Evidence`; claims stream from its content items.
5. Contract item **`401`/`403`** — needs the auth posture.
6. Contract item **`annotations`** — needs the offset unit.
7. Image input — schema half now; behaviour half needs the attachment service.

## 8. Questions that block work

1. **`start_index` — code points, UTF-16 units, or bytes?** Whatever is chosen
   goes into the contract with a conformance test in Devanagari and Tamil.
2. **`401`/`403` — reserve, implement, or make pluggable?** The contract asks for
   codes ADR-0002 §2.3 says cannot happen.
3. **`502`/`504` — ever really returned?** Defined "before streaming began", but
   JSON mode never begins streaming and the contract's own outage example is a
   `200`.
4. **Should `406`/`413`/`415` join the contract?**
5. **What does `confidence` mean per status?** A refusal's 98 and an answer's 92
   are not the same measurement.

## 9. Doc hygiene owed

- This file and `0002-v1/` predate the `<github-issue>-short-slug.md` convention.
  Fold into [`87-v1-turn-api.md`](../87-v1-turn-api.md) and delete the directory,
  `conformance-fixes.md` and `real-flow-with-stubs.md` with it.
- `docs/dssturnandhexagon.html` draws a two-axis `status`/`kind` outcome that
  neither the contract nor the code has. Delete or redraw.
