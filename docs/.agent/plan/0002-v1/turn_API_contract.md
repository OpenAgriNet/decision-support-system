# `POST /v1/turns` — the structure as built


The endpoint runs with a stubbed core. 1,873 lines of `src/`, 197 tests, 100%
line coverage. This document describes **what is on disk today** and what is
left.

```bash
uv run uvicorn --factory dss.entrypoint.app:create_app --port 8077
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
   │ mapping.py               │  wire -> domain, pure
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
        │  calls, in sequence
   ┌────▼──────────────────────────────────────┐
   │ core/moderation.screen(turn, llm)         │  INSIDE
   │   -> reject?  terminal, no claims          │
   │ core/intent.recognise_intent(turn, llm)   │
   │   -> None?    terminal, no_match           │
   │ core/channel.compose(turn, intent, llm)   │
   └────┬──────────────────────────────────────┘
        │  yields TurnStarted, Claim…, TurnFinished
   ┌────▼─────────────────────┐
   │ sse.py  or  router._single │  events -> frames, or drained to one body
   └──────────────────────────┘

   evidence, written by the runner at each step:
     ports/sinks.py  ->  adapters/sinks/file.py  ->  var/evidence/*.jsonl
```

Two arrows, opposite directions — that inversion is the pattern:

- **runtime:** transport → port → runner → core → interface → adapter
- **imports:** adapter → interface ← core. The interface imports nothing.

---

## 2. Every file, and what it holds

### Inside the hexagon

`core/` imports only `__future__`, `enum`, `typing`, `pydantic`, and itself.
Audited by AST, enforced by `test_core_isolation.py`.

| File | Holds |
|---|---|
| `core/shared/models.py` | the domain language: `UserTurn`, `TurnContext`, `HistoryEntry`, `Role`, `Channel`, `Location`, `Point`, `TurnStatus`, `Cause`, `Source`, `SourceKind`, `TextBlock`, `RefusalBlock`, `TurnOutcome`, `TurnStarted`, `Claim`, `TurnFinished`, `TurnEvent` |
| `core/shared/llm.py` | `LLM` — one method, `structured(system_prompt, user_query, schema)`. No `temperature`, no `model=`, no API key |
| `core/shared/errors.py` | `DssError`, `ProviderUnavailable` — what adapters wrap vendor exceptions into |
| `core/moderation/models.py` | `Outcome` (proceed/reject/clarify/no_match), `Screening` |
| `core/moderation/service.py` | `screen(turn, *, llm)` — **`STUB(#82)`**, a deny word list |
| `core/intent/models.py` | `Intent`, `ActionType` |
| `core/intent/service.py` | `recognise_intent(turn, *, llm)`, `CONFIDENCE_FLOOR = 0.6` — the floor is **real** and survives the stub |
| `core/channel/models.py` | `ComposedAnswer` |
| `core/channel/service.py` | `compose(turn, intent, *, llm)`, `no_match_answer(turn)` — **`STUB(#84)`**, fixed sentences |
| `ports/turn.py` | `TurnRunner` — the driving port |
| `ports/sinks.py` | `TurnSink` (required), `TelemetrySink` (optional) — driven ports |

**Why `llm.py` is in `core/shared/` and not `ports/`:** `core/` is what declares
that need, and a Protocol is satisfied structurally — `adapters/llm/stub.py`
implements it without importing it, so the dependency still points inward. The
sinks stayed in `ports/` because the runner *drives* them, which is the textbook
driven-port shape.

### Outside

| File | Holds |
|---|---|
| `adapters/http/v1/schema.py` | the wire models. **camelCase lives here and nowhere else** — `_WireIn` (`populate_by_name=False`, so only camelCase is accepted) and `_WireOut` (built by field name, dumped `by_alias=True`). Plus `component_schemas()` for the OpenAPI document |
| `adapters/http/v1/mapping.py` | wire ↔ domain, pure functions. The clock and the minted id are arguments |
| `adapters/http/v1/sse.py` | `Stream` — owns the event names and the sequence counter |
| `adapters/http/v1/router.py` | the HTTP rules; admission ordered cheapest-rejection-first |
| `adapters/http/v1/problem.py` | 4xx/5xx bodies |
| `adapters/llm/stub.py` | `StubLLM` — **`STUB(#83)`**, canned answers, records every ask |
| `adapters/sinks/file.py` | `FileTurnSink`, `FileTelemetrySink` — JSON Lines. Drops `geometry` on write |
| `adapters/sinks/memory.py` | `MemoryTurnSink` — **`STUB(#85)`** |
| `adapters/sinks/stdout.py` | `StdoutTelemetrySink` |
| `orchestration/core_runner.py` | `CoreRunner` — the order, and nothing else |
| `orchestration/stub_runner.py` | `StubRunner` — **`STUB(#81)`**, canned events, reads the turn for nothing |
| `entrypoint/app.py` | builds the `FastAPI` app, publishes the wire schemas into `components.schemas` |
| `entrypoint/composition.py` | **the only file that names concrete classes** |
| `entrypoint/settings.py` | caps, release id, evidence dir and URL. `DSS_`-prefixed env overrides |

### Tests

| Path | Drives | Doubles |
|---|---|---|
| `tests/unit/adapters/http/v1/` | `mapping.py`, `sse.py`, `_wants_stream` | none — pure functions |
| `tests/unit/core/**` | the three core services | `StubLLM` |
| `tests/unit/adapters/llm/` | `StubLLM` itself | none |
| `tests/unit/entrypoint/` | `create_app`, `build_runner` | env via `monkeypatch` |
| `tests/integration/entrypoint/` | the HTTP layer | `FakeRunner` — no core |
| `tests/integration/orchestration/` | `CoreRunner` over real core | `StubLLM`, `FakeTurnSink`, `FakeTelemetrySink` |
| `tests/integration/adapters/sinks/` | the three sink adapters | `tmp_path`, `capsys` |
| `tests/conformance/v1/` | camelCase both ways, OpenAPI ref resolution | `FakeRunner` |
| `tests/support/fakes.py` | `FakeRunner(events, fail_after=)`, `FakeTurnSink(fail_on=)`, `FakeTelemetrySink(fail=)` | hand-written, never `MagicMock` |

**Three boundary suites, each proven able to fail** by planting a violation:

| Suite | Rule |
|---|---|
| `test_framework_boundary.py` | `core/` imports no agent framework |
| `test_transport_boundary.py` | `adapters/http/**` and `entrypoint/**` may import `ports/**` and `core.shared` — nothing else from `core/` |
| `test_core_isolation.py` | nothing inside imports `adapters`/`orchestration`/`entrypoint`/`config`, or anything impure (clock, filesystem, env, randomness, network) |

---

## 3. Decisions this structure records

| Decision | Where it shows up |
|---|---|
| One endpoint; `Accept` selects JSON or SSE | `router.py::_wants_stream` — no streaming flag anywhere |
| Streaming is the transport's business, not the runner's | the port returns an iterator; `sse.py` owns framing and the counter |
| camelCase on the wire, snake_case in Python | aliases on `_WireIn`/`_WireOut` only |
| Outcome is one axis; `unavailable` is the failure value | `TurnStatus`, six values, no `kind` |
| Citations per block | `TextBlock.source_ids` — **contract disagrees, see §4** |
| No authentication | no auth middleware; `401`/`403` never emitted |
| Two evidence tiers with opposite failure rules | `ports/sinks.py`; telemetry swallowed, turn record propagates |
| `geometry` never written to a sink | `adapters/sinks/file.py::_turn` |
| Axis order exists in one function | `mapping.py::_point`; `Point` holds named floats |
| The composition root is the process entry | `entrypoint/composition.py` (ADR-0006) |
| FastAPI on uvicorn | ADR-0006 |
| Sequential runner, no graph | `core_runner.py` — ADR-0001 makes `pydantic-graph` the escalation, not the start |

**Nothing imports an agent framework.** `pydantic-ai-slim` is a dependency; no
module uses it. `core/` is provably framework-independent; framework
*swappability* is designed for and has not been exercised once.

**The tool-calling loop will not go behind a port** — `10-planner-agent-poc.md`
rejects that deliberately, so switching frameworks later means rewriting
`orchestration/planner.py`. One module, with `core/` and `ports/` untouched.

---

## 4. Contract conformance — seven closed, four open

Responses are now validated against the published spec by
`tests/conformance/v1/test_against_openapi.py`, which loads
`docs/api-contracts/openapi.yaml` and checks rendered bodies with `jsonschema`.
That test is what closed these — and it found two gaps the hand-written
conformance tests had missed.

### Closed

| # | Was | Now |
|---|---|---|
| 1 | `outcome.confidence` absent (**required**) | present; `STUB(#86)` supplies a number per status |
| 1b | `outcome.cause` omitted when null (**required**, nullable) | always emitted, via an explicit `model_serializer` |
| 3 | `message.error` absent | `TurnError` — `code`, `message`, `retryable`, `retryAfterSeconds` |
| 4 | `envelopeVersion` + `dssRelease` (**rejected** — `additionalProperties: false`) | one `version` = the DSS release |
| 5 | `responseMessageId` | `resMessageId` |
| 8 | `sequenceNumber` started at 0 | starts at 1 |
| 9 | `traceId` minted from `traceparent`; body value rejected | `traceId` **is** the caller's `transactionId`, now required. `traceparent` still starts the span |

Two of those were not on the original list. The validator found them:
`envelopeVersion` was **illegal** rather than merely misnamed, and response
`messageId` is required but was dropped whenever the caller omitted one — the
transport now mints it.

### Open

| # | `openapi.yaml` | Code | Blocked on |
|---|---|---|---|
| # | `openapi.yaml` | Code | Blocked on |
|---|---|---|---|
| 2 | `content[].annotations` with `start_index`/`end_index` | `sourceIds: []` — an extra field the spec permits, so responses still validate; `annotations` is simply absent | **the unit of an offset** |
| 6 | `401 · 403 · 502 · 504` | not emitted; `406 · 413 · 415` invented | **auth posture** |
| 7 | `content[].type` is `text \| image` | `text` only | the attachment service contract |
| 10 | `tracestate` | not read | nothing — no behaviour is specified for it |

Fixing #9 **inverted two existing tests** — they asserted that a body-supplied
trace id was rejected, which was a security instinct the contract had already
decided against. That is a rule change rather than a bug fix, and worth a
reviewer's eye.

The lesson from #1b is the reusable one: `exclude_none=True` silently drops any
null, so **every field the contract marks `required` and nullable is a latent bug
of the same shape.** A hand-written conformance test cannot catch that class;
validating against the spec can.

---

## 5. The flow this will become

`10-planner-agent-poc.md` fixes the real shape. Today's runner is the middle
column of it, straightened out.

```
intent ∥ moderation                parallel — ADR-0003, not sequential
discovery(intent.asks)             starts when intent lands; does NOT wait for the verdict
plan(discovery, verdict, skills)   awaits the verdict before ANY tool call   ← the barrier
sufficiency(evidence, asks)        plain code, no LLM, ever
compose(evidence, identity)        content items + sources
```

Three differences from `core_runner.py` as written:

- intent and moderation are **concurrent**
- **discovery may cross the moderation barrier** — a discovery query is read-only
  and disposable; a provider invocation is not
- the gate is **"no tool call before the verdict resolves"**, not "moderation
  first". A rejected turn may already have run discovery, and that is correct

New files that flow needs: `core/planner/{models,sufficiency}.py`,
`orchestration/planner.py`, `ports/discovery.py`, `ports/invocation.py`,
`adapters/discovery/stub.py`, `adapters/invocation/stub.py`.

The barrier is the highest-value case in the whole plan and is testable with
stubs: a verdict that records when it was awaited, an invocation stub that
records when it was called, assert the ordering — then reorder the runner and
watch it fail.

---

## 6. Order

1. ~~Items 1, 1b, 3, 4, 5, 8, 9~~ — **done**; responses validate against the spec.
2. The §5 flow with stubs — **the barrier first**.
3. Item **6** — needs the auth posture.
4. Item **2** — needs the offset unit.
5. Item **7** — schema half; behaviour half needs another team.

The server is now conformant on everything that is not itself an open contract
question.

## 7. Questions that block work

1. **`start_index` — code points, UTF-16 units, or bytes?** Whatever is chosen
   goes into the contract with a conformance test in Devanagari and Tamil.
2. **`401`/`403` — reserve, implement, or pluggable?** The contract asks for
   codes ADR-0002 §2.3 says cannot happen.
3. **`502`/`504` — ever really returned?** Defined "before streaming began", but
   JSON mode never begins streaming and the proposal's own outage example is
   `200`.
4. **Should `406`/`413`/`415` join the contract?** They cover cases it requires
   but gives no code for.

## 8. What a `feat/79` merge has to settle

- `ports/llm.py` exists there; here the interface is `core/shared/llm.py`.
- `Intent` there has `asks: tuple[Ask, ...]`; here `primary_domain` + `confidence`.
- `UserTurn` there has `original_query`/`enriched_query`, ids on the turn,
  `UserDetails.phone`, `ReferenceToken`. The `ref` question is unanswered.
- `Outcome` names two different things — moderation's enum and the wire object.
- ADR numbers: `feat/79` holds 0003–0005 and a second 0002; ADR-0006 is here.
- `CLAUDE.md` and `HEXAGONAL_ARCHITECTURE.md` §3.1 still place `LLMProvider` in
  `ports/`.
