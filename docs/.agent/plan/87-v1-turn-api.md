# Spec 87 — `POST /v1/turns`

**Issue:** #87 
**Contract:** `docs/api-contracts/openapi.yaml` (authoritative for the wire)
**Decisions:** ADR-0001 (framework), ADR-0002 (contract), ADR-0006 (FastAPI)

Numbering note: `feat/79` cites specs 0002–0004 from code comments, so this one
takes its issue number, as `10-planner-agent-poc.md` does.

---

## 1. Scope

One HTTP endpoint that accepts a turn and returns an answer, with the reasoning
stubbed. Everything structural is real: the wire contract, the driving port, the
runner's ordering, the evidence sinks, the boundaries between layers.

**In scope**

- `POST /v1/turns` — JSON or SSE, selected by `Accept`
- the request/response envelope and its mapping to domain types
- the turn lifecycle and its failure semantics
- two evidence tiers
- stub implementations of moderation, intent recognition and composition

**Not in scope**

- any real language model, or any network call
- provider discovery, planning, capability invocation, sufficiency
- image and file input
- authentication of any kind
- retries and duplicate detection

---

## 2. Interface

### 2.1 Transport

| | |
|---|---|
| Method and path | `POST /v1/turns` |
| Request | `application/json`, optionally `Content-Encoding: gzip` |
| Response | `application/json`, or `text/event-stream` when `Accept` asks for it |
| Auth | none. The DSS never emits `401` or `403` (ADR-0002 §2.3) |

**The endpoint does not take a streaming flag.** `Accept` decides:
`text/event-stream` anywhere in the header selects SSE; `application/json`,
`*/*`, `application/*`, or an absent header selects JSON; anything else is `406`.

### 2.2 Request

`{context, message}`. camelCase, `additionalProperties: false` throughout — a
snake_case key is an unknown field.

```
context.id             const "api.dss.turn"        required
context.timestamp      RFC 3339                     required
context.sessionId      conversation key             required
context.transactionId  the correlation key          required  ← echoed as traceId
context.version        caller's envelope version    optional, ignored
context.messageId      caller's message id          optional, minted if absent

message.input[]        role + typed content parts   required, min 1
message.userContext[]  typed identity facts         optional
message.attributes     language, channel, location, response cap
```

Content parts are `{type: "text", text}` in v1.

### 2.3 Response

The same canonical turn object in both modes.

```
context.id             const "api.dss.turn"
context.version        the DSS release that handled the turn
context.timestamp      when the frame was built
context.messageId      the caller's, or the minted one
context.sessionId      echoed
context.traceId        the caller's transactionId
context.resMessageId   the DSS's own id for this response
context.sequenceNumber SSE only, ≥ 1, monotonic and gapless

message.outcome        { status, cause, confidence }   all three always present
message.content[]      { type: "text"|"refusal", text, sourceIds[] }
message.sources[]      { id, name, kind, url }
message.error          { code, message, retryable, retryAfterSeconds } — failures only
```

`status` is one of `answered · partially_answered · rejected · no_match ·
requires_input · unavailable`. **One axis** — `unavailable` is the
execution-failure value, so there is no second `kind` field.

`cause` is `null` when answered and a machine-readable string otherwise. It is
**required and nullable**, so it is emitted as `null` rather than omitted.

### 2.4 Events

| Event | Carries |
|---|---|
| `turn.created` | context only |
| `claim.completed` | context + one reviewed content item |
| `turn.completed` | the full turn |
| `turn.failed` | the full turn, when `status` is `unavailable` |

The terminal event is authoritative. A client does not infer the result from the
connection closing. There is no `id:` line, because the stream is not resumable —
on a dropped connection the DSS finishes server-side.

### 2.5 Status codes

`200` for every turn the DSS processed, whatever the outcome — a refusal, a
no-match and a request for more input are all `200`.

| Code | When |
|---|---|
| `400` | body is not JSON, is not UTF-8, or claims gzip and is not |
| `406` | `Accept` can be satisfied with neither media type |
| `413` | body exceeds the cap **after** decompression |
| `415` | `Content-Type` is not `application/json` |
| `422` | well-formed JSON that violates the contract |
| `429` | concurrency cap reached; carries `Retry-After` |
| `503` | the DSS is not ready |

`406`, `413` and `415` are additions to the contract, which requires handling
those cases but names no code for them (§8).

---

## 3. Types

### 3.1 Domain — `core/shared/models.py`

Plain Pydantic. No framework, no vendor SDK, no I/O.

```
UserTurn      query, source_lang, target_lang, channel, user_id, history, location,
              response_max_chars
TurnContext   trace_id, session_id, message_id, transaction_id
HistoryEntry  role, content
Location      region, area, geometry: Point
Point         lon, lat                     ← named floats, not a positional pair
TurnOutcome   status, confidence, cause, retry_after_seconds
TextBlock     text, source_ids
RefusalBlock  text
Source        id, name, kind, url
TurnEvent     TurnStarted | Claim | TurnFinished
```

Two shapes are deliberate:

- **`Point` holds named floats.** GeoJSON carries `[lon, lat]` positionally and
  the existing deployments send it reversed. The ordering exists in one mapping
  function; inside the hexagon the mistake is unrepresentable.
- **`Cause` is a closed enum** although the wire set is open. Core may only emit
  causes it knows; openness is a promise to callers about future releases.

### 3.2 Wire — `adapters/http/v1/schema.py`

Field names live here and nowhere else. Two bases:

- `_WireIn` — `alias_generator=to_camel`, `populate_by_name=False`. Only
  camelCase is accepted.
- `_WireOut` — same generator, `populate_by_name=True`, because mapping
  constructs these by field name. Dumped with `by_alias=True`.

### 3.3 Mapping — `adapters/http/v1/mapping.py`

Pure functions. The clock and the minted id are arguments, so every rendering is
deterministic. Rules this module owns and nothing else does:

1. `geometry.coordinates[0]` is longitude, `[1]` is latitude.
2. The **last `user` message** is the current query; everything before it is
   history. A thread with no user message is a `422`.
3. Several content parts in one message join with a space.
4. The first `identity` entry supplies `user_id`; none present means
   `"anonymous"`.
5. `traceId` is the caller's `transactionId`.
6. `messageId` is the caller's, or minted.
7. `cause` is serialized even when `null`.

---

## 4. Behaviour

### 4.1 Admission

Ordered cheapest-rejection-first, so a flood of bad requests costs as little as
possible:

```
capacity (429) → readiness (503) → Content-Type (415) → read body
   → gunzip + cap (400 / 413) → JSON parse (400) → schema (422)
   → Accept (406) → map (422) → run
```

**Once the first response byte is written the status code cannot change.** Every
failure discovered after that becomes a terminal event inside a `200`.

### 4.2 Turn lifecycle

`orchestration/core_runner.py` owns the order and nothing else:

```
TurnStarted
  moderation.screen(turn, llm)     not PROCEED → terminal rejected, no claims
  intent.recognise_intent(turn, llm)   None    → terminal no_match
  channel.compose(turn, intent, llm)
    one Claim per content item
TurnFinished
```

Every branch reads a value another module decided. The runner contains **no `if`
with business meaning** — the moment it grows one, a rule has escaped `core/`.

### 4.3 Failure semantics

| Failure | Streaming | JSON |
|---|---|---|
| moderation rejects | `200`, `turn.completed`, `rejected` | `200`, `rejected` |
| nothing can answer | `200`, `turn.completed`, `no_match` | `200`, `no_match` |
| dependency down | `200`, `turn.failed`, `unavailable` + `error` | `200`, `unavailable` + `error` |
| crash mid-turn | frames already sent stand; `turn.failed`, `internal` | `200`, `unavailable` |
| client disconnects | nothing arrives; the turn finishes server-side | n/a |

A dependency failure **never** surfaces as a 5xx. It lands in `outcome`.

### 4.4 Evidence

Two tiers, opposite rules, because one port could carry neither correctly.

| Port | Holds | Required | On write failure |
|---|---|---|---|
| `TurnSink` | the question, the answer, every refusal | yes — it is the audit trail | **the turn fails** |
| `TelemetrySink` | stage, outcome, join keys. **Never** user content | no; stdout by default | swallowed |

`geometry` is dropped on write; `region` and `area` are kept. A stable user id
beside a precise point, across many turns, is a home address.

Both tiers key on `traceId`. `FileTurnSink` and `FileTelemetrySink` write JSON
Lines under `Settings.evidence_dir`, standing in for the external evidence API
until it exists (`DSS_EVIDENCE_URL` is declared and warns if set).

---

## 5. Invariants

Each of these is a passing test, and each has been shown to fail when broken.

| Invariant | Enforced by |
|---|---|
| `core/` imports no agent framework | `test_framework_boundary.py` |
| The transport may not call a core service | `test_transport_boundary.py` |
| Nothing inside the hexagon imports outward, or anything impure | `test_core_isolation.py` |
| Every runner satisfies the port, exercised through it | `test_port_conformance.py` |
| Responses validate against `openapi.yaml` | `test_against_openapi.py` |
| Every `$ref` in the generated document resolves | `test_openapi_document.py` |
| The wire is camelCase, both directions | `test_wire_casing.py` |
| A rejected turn never asks for an `Intent` | `test_core_runner.py` |
| A required sink failure fails the turn; an optional one does not | `test_core_runner.py` |

**205 tests, 100% line coverage**, gated at 85%.

---

## 6. What is stubbed

Every stub is marked `STUB(#nn)` in the source.

| Marker | Where | Behaviour | Replaced by |
|---|---|---|---|
| `#81` | `orchestration/stub_runner.py` | canned events, reads the turn for nothing | superseded by `CoreRunner` |
| `#82` | `core/moderation/service.py` | a deny word list, so the reject path is deterministic | policy evaluator |
| `#83` | `adapters/llm/stub.py` | canned typed answers, records every ask | a real `LLM` adapter |
| `#84` | `core/channel/service.py` | fixed sentences and one source | composer + reviewer |
| `#85` | `adapters/sinks/memory.py` | a dict | a durable store |
| `#86` | `orchestration/core_runner.py` | a confidence number per status | intent and composition reporting their own |

Signatures are final; bodies are disposable. If a real implementation needs a
different signature, that is a design finding rather than a refactor.

---

## 7. Structure

```
INSIDE                              OUTSIDE
core/shared    models, llm, errors  entrypoint/     app, composition, settings
core/moderation  intent  channel    adapters/http/v1  router, schema, mapping, sse, problem
ports/         turn, sinks          orchestration/  core_runner
                                    adapters/       llm, sinks
```

Imports point inward, always. Control flows both ways.

`core/shared/llm.py` holds the LLM interface rather than `ports/` because `core/`
is what declares that need, and a Protocol is satisfied structurally — the
adapter implements it without importing it. The sinks are in `ports/` because the
runner *drives* them.

**Framework independence:** `core/` imports only `__future__`, `enum`, `typing`,
`pydantic` and itself. Nothing anywhere imports `pydantic_ai`. Framework
*swappability* is therefore designed for but unexercised — and per
`10-planner-agent-poc.md` the eventual tool-calling loop will deliberately **not**
sit behind a port, so switching frameworks will mean rewriting
`orchestration/planner.py`: one module, with `core/` and `ports/` untouched.

---

## 8. Not yet conformant

| Contract | Code | Blocked on |
|---|---|---|
| `content[].annotations` with `start_index`/`end_index` | `sourceIds[]` — an extra the spec permits, so responses still validate | **the unit of an offset**: code points, UTF-16 units, or bytes |
| `401 · 403 · 502 · 504` | not emitted; `406 · 413 · 415` added instead | **the auth posture** — the contract asks for codes ADR-0002 §2.3 says cannot happen |
| `content[].type` is `text \| image` | `text` only | the attachment service contract |
| `tracestate` | not read | nothing — the contract specifies no behaviour for it |

## 9. Open questions

1. **`start_index` — what is one unit?** Whatever is chosen goes into the
   contract with a conformance test in Devanagari and Tamil. Get it wrong and
   every citation lands on the wrong words in exactly the scripts this system
   serves.
2. **`401`/`403` — reserve, implement, or make pluggable?**
3. **`502`/`504` — ever really returned?** They are defined "before streaming
   began", but JSON mode never begins streaming and the contract's own
   provider-outage example is a `200`.
4. **Should `406`/`413`/`415` join the contract?**
5. **What does `confidence` mean per status?** A refusal's 98 and an answer's 92
   are not the same measurement.
6. **`resMessageId` versus `messageId`** — the contract's example gives them the
   same value with no stated difference.

## 10. Next

The turn flow is not yet the real one. `10-planner-agent-poc.md` fixes it:
intent and moderation run **concurrently**, discovery starts when intent lands
and is permitted to cross the moderation barrier, and the gate becomes **"no tool
call before the verdict resolves"** rather than "moderation runs first".

The barrier is the highest-value case to build next and is testable with stubs.
