# DSS API Contract — `UserTurn` (chat / decision turn)

> Status: **Draft (v1 design)**. This contract normalizes the `stream_chat_messages`
> surface from the three OAN participating-deployment APIs (amul, bharat, mh) into a
> single DSS interface. Companion reading: `docs/DSS_ARCHITECTURE.md` §5.1 (request
> envelope), §2.1 (provides), §6.1 (PII posture), §7 (failure behaviour), §8.3 (open
> items). Where this contract and architecture §8 disagree, §8 is the authority — flag drift in a PR.
> The design decisions marked **[decided]** below were settled in the v1 contract grill;
> §10 is the decision log.

This is the contract for **parties calling the DSS** (the Experience API of a
participating deployment). It replaces the per-tenant `stream_chat_messages(...)`
function signatures with one transport-level envelope in and one event stream out.

---

## 1. Where this comes from — the three legacy signatures

The DSS subsumes `app/services/chat.py::stream_chat_messages` from all three repos.
Their parameters, unioned:

| Legacy param | amul | bharat | mh | Disposition in DSS contract |
|---|:--:|:--:|:--:|---|
| `query` | ✅ | ✅ | ✅ | **Kept** — `query` (body) |
| `session_id` | ✅ | ✅ | ✅ | **Moved to header** — `X-Session-Id` (§2.1) |
| `source_lang` | ✅ | ✅ | ✅ | **Kept** — `source_lang` (body) |
| `target_lang` | ✅ | ✅ | ✅ | **Kept** — `target_lang` (body) |
| `user_id` | ✅ | ✅ | ✅ | **Moved into `subject_ref`** — see §3 |
| `history` | ✅ | ✅ | ✅ | **Kept** — `history` (body), neutral `TurnHistoryEntry[]`; DSS reads, never writes |
| `user_info` / `current_user` (JWT dict) | ✅ | ✅ | ✅ | **Dropped from wire** — see §3 (opaque ref) |
| `channel` | ✅ | ✅ (JWT) | ❌ | **Kept** — `channel` (body) |
| `background_tasks` | ✅ | ✅ | ✅ | **Dropped** — FastAPI internal; DSS owns its own async work |
| `use_translation_pipeline` | ✅ | ❌ | ❌ | **Dropped** — translation mode is tenant/Identity config (§5.1), not a caller flag |
| `pipeline_profile` | ✅ | ❌ | ❌ | **Dropped** — LLM tier selection is DSS-internal |
| `qid` | ❌ | ✅ | ❌ | **Moved to header** — `X-Trace-Id` (§2.1) |
| `is_image_analysis` | ❌ | ✅ | ❌ | **Kept** — `request_options.modality = "image"` + image endpoint |
| `latitude` / `longitude` | ❌ | ✅ | ❌ | **Kept** — `multipart` form fields on `/v1/turns:analyze-image` (§8.3), not the JSON envelope |

**Output, all three:** `AsyncGenerator[str, None]` — a `text/event-stream` of plain
text chunks, with moderation declines and error messages muxed inline as text, and
moderation category / provenance / route / confidence computed internally but **never
surfaced to the caller** (they went only to Langfuse/telemetry). This contract promotes
that dropped structure into typed terminal events with uniform fields (§5).

---

## 2. Transport

Transport is **REST over HTTP with Server-Sent Events (SSE)** for streaming **[decided]**;
gRPC (server-streaming) and in-process were the rejected alternatives — see §10 for the rationale.

| Operation | Method + path | Body | Response |
|---|---|---|---|
| Text turn | `POST /v1/turns:stream` | `application/json` (§4) | `text/event-stream` (§5) |
| Image turn | `POST /v1/turns:analyze-image` | `multipart/form-data` (envelope fields + `image` file) | `text/event-stream` (§5) |
| Non-streaming | `POST /v1/turns` | `application/json` | `application/json` — the terminal object (§5.3) with the full answer inlined |

`POST` (not the legacy `GET /chat/?...`) because `history` and future context do not
belong in a query string, and the image flow needs `multipart`.

### 2.1 Required request headers

Correlation and conversation identifiers travel in **headers**, not the JSON body —
they are cross-cutting transport concerns (logging, tracing, routing) that every
operation shares and that middleware reads without parsing the body.

| Header | Required | Meaning |
|---|:--:|---|
| `X-Session-Id` | ✅ | Conversation key. DSS reads `history` under it; the Experience API owns persistence (§1.2). |
| `X-Trace-Id` | optional | Per-Turn correlation id (was bharat `qid`). Must be an **opaque, non-personal** token; the DSS **mints one when absent** so evidence never keys on caller PII. Doubles as the idempotency key (§2.4) and the operational-evidence key (§6). Echoed back on the response. |
| `Content-Type` | ✅ | `application/json` or `multipart/form-data`. |

There is **one** per-Turn identifier: `X-Trace-Id` *is* the "interaction identifier" of
architecture §6.3 (they were unified into a single id — see §10). `X-Session-Id` and
`X-Trace-Id` are echoed back on the response
(`Access-Control-Expose-Headers: X-Session-Id, X-Trace-Id`).

### 2.2 Versioning **[decided]**

The API version is carried in the **URI path** (`/v1/...`). Additive, backward-compatible
fields do **not** bump the version; only a breaking change to the envelope or terminal
schema goes to `/v2`. Callers pin the path they integrate against.

### 2.3 Trust boundary & authentication **[decided]**

The DSS performs **no app-level authentication** — neither of a person (§1.2) nor of the
calling service. Its trust boundary is the **network perimeter**: the deployment MUST
ensure that only the Experience API can route to the DSS port (network policy / service
mesh). Consequences:

- The DSS itself never issues `401` / `403`. Caller and person authentication are
  entirely the Experience edge's responsibility.
- **Rate-limiting is Experience-edge-owned.** The DSS may self-protect with a global
  concurrency cap but owns no per-user quotas.

### 2.4 Idempotency **[decided]**

`X-Trace-Id` is the idempotency key. A Turn replayed with a `trace_id` already seen within
a short window returns the **prior Turn's terminal outcome** instead of re-executing
side-effecting tools. This is **best-effort dedup, not distributed exactly-once** —
compensation across already-committed Provider side-effects is a §8 open item (§7 forbids
changing Provider-owned state).

### 2.5 Streaming & reconnection **[decided]**

The SSE stream is **non-resumable** in v1. If the connection drops mid-Turn, the DSS still
**finalizes the Turn server-side** (the answer is persisted to the session by the
Experience-owned store, as the legacy already does on disconnect). The client recovers by
**re-issuing with the same `X-Session-Id`** — the completed Turn is now in history —
rather than resuming the byte stream. `Last-Event-ID` replay is a later enhancement
(likely required for voice barge-in).

### 2.6 HTTP status convention **[decided]**

A Turn the DSS *processed* returns **`200`** regardless of outcome — `rejected` /
`no_match` / `error` live in the terminal event's `status` (§5.2), **not** the HTTP code.
Non-2xx is reserved for transport/validation faults the DSS raises:

| Code | Meaning |
|---|---|
| `400` | Malformed envelope (unparseable body, missing required header) |
| `422` | Schema violation (well-formed but invalid field) |
| `503` | DSS unavailable / overloaded |

`401` / `403` are **not** DSS-issued (§2.3).

---

## 3. Identity & PII posture — **opaque reference only** (Posture A, §6.1)

**What the contract pins vs. defers.** The contract fixes exactly one property of
`subject_ref`: it is **opaque to the DSS** — the DSS never inspects, resolves, or
validates it. *Who* resolves `ref`, *what* it resolves to, and *who* validates its
signature are **deferred** to the Network Consumer Adapter design (no resolver exists in
the current repos), and are not part of this caller contract. **[decided]**

The envelope carries **no raw personal data** — no `phone`, `farmer_id`, `unique_id`,
name, email, or raw JWT claims dict. Identity is a single `subject_ref` object minted by
the Experience API:

```jsonc
"subject_ref": {
  "user_id":    "string",        // analytics/correlation id ONLY; "anonymous" if unknown.
                                  //   Not a credential, not resolvable to a person by the DSS.
  "ref":        "string|null",   // Provider-scoped opaque token, meaningful only to the
                                  //   intended Provider. DSS transports it to the Network
                                  //   Consumer Adapter; never inspects, resolves, persists, or logs it.
  "issuer":     "string|null",   // who minted the ref (Experience API / tenant), for audit
  "expires_at": "string|null"    // RFC3339 validity bound; DSS treats expired refs as absent
}
```

- `user_id` lives **inside** `subject_ref` (there is no top-level `user_id`).
- `ref` is **required for any Turn that needs a Provider capability acting on the
  caller's behalf**; the whole object may be `null`/anonymous for public queries.
- Personal payloads a Provider capability needs travel the protected
  Experience→Provider path, not the prompt (§6.1). The DSS never fetches farmer context
  from a raw phone.

Consequences vs. legacy:

- amul's `user_info["phone"]` → farmer-context bundle + loan `mobile`: resolution moves
  behind `subject_ref.ref` (Experience layer resolves, or a Provider capability does).
- bharat's rich JWT (`mobile`, `name`, `role`, `locations`, telemetry_context): stays at
  the Experience layer; only non-personal routing signals (if any) may be projected via
  Context Providers (§4.1) — never raw identifiers.
- mh's `farmer_id` / `unique_id`: carried, if at all, as part of what `ref` resolves to
  at the Provider — not on the envelope.

> **PII through free text — see §8.2.** `subject_ref` removes *structured* PII, but
> `query` and `history` are free text. The decided posture is that the **Experience API
> scrubs** these before the DSS sees them; sink-layer redaction (§6.2) is defense-in-depth.

---

## 4. Request envelope (`UserTurn`)

Body of `POST /v1/turns:stream` (with `X-Session-Id` / `X-Trace-Id` in headers, §2.1):

```jsonc
{
  // ── required core ─────────────────────────────────────────────
  "query":        "string",              // user utterance (assumed PII-scrubbed by caller, §8.2)
  "source_lang":  "gu|hi|mr|en|...",     // inbound language (drives routing/skill filter)
  "target_lang":  "gu|hi|mr|en|...",     // outbound language (drives composition/review)
  "channel":      "web|whatsapp|voice|sms|...",  // delivery channel (shaping only, §3.7)

  // ── identity (opaque only, §3) ────────────────────────────────
  "subject_ref": {
    "user_id":    "string",              // analytics id; default "anonymous"
    "ref":        "string|null",
    "issuer":     "string|null",
    "expires_at": "string|null"
  },

  // ── prior context ─────────────────────────────────────────────
  "history": [ /* TurnHistoryEntry[] — framework-neutral { role, content, … };
                  NOT the orchestration framework's ModelMessage type (ADR-0001 boundary).
                  Exact fields deferred (§8.1). */ ],

  // ── optional per-turn options ─────────────────────────────────
  "request_options": {
    "modality":           "text|image",  // "image" → analyze-image endpoint
    "response_max_chars": 0,             // channel length guidance (was amul response_max_chars)
    "stream":             true
  }
}
```

Field rules:

- `query`, `source_lang`, `target_lang`, `channel` are **required** in the body;
  `X-Session-Id` is **required** in headers (`X-Trace-Id` optional, minted if absent).
- `subject_ref.ref` is required for capability-backed Turns; `subject_ref` may be
  anonymous otherwise.
- `history` may be `[]`. Entries use the neutral **`TurnHistoryEntry`** schema; the DSS
  maps to/from its internal representation at the boundary. The DSS **reads** history and
  never writes to the Experience-owned store (§1.2) — persisting the new Turn is the
  Experience API's job.
- `request_options` and all its members are optional.
- Anything not listed here is **rejected** — no raw identity dict, no framework handles
  (`background_tasks`), no LLM-tier flags (`pipeline_profile`, `use_translation_pipeline`),
  no `session_id`/`qid` in the body (they are headers now).

---

## 5. Response — hybrid: text stream + uniform terminal event

The answer body streams as plain text (legacy-compatible for existing renderers), then
**exactly one terminal event** closes the Turn.

### 5.1 Content deltas

```
event: content.delta
data: {"text": "<partial answer text>"}      // repeated 0..N times — the streamed answer
```

### 5.2 Terminal event — one uniform schema for every outcome

Every terminal event carries the **same four fields**, so a caller parses one shape
regardless of outcome. Non-`answered` terminals may arrive with **zero preceding
`content.delta`** (e.g. moderation reject before any answer).

```jsonc
// data schema, identical across all terminal events:
{
  "status":  "answered|rejected|no_match|needs_clarification|error",
  "cause":   "string|null",   // machine code; null only when status=answered
  "details": { /* object */ },// structured context for the outcome (provenance/confidence
                              //   on answered; category/retryable/missing on the others)
  "text":    "string"         // human-readable message (final answer when answered)
}
```

| Event | `status` | `cause` (examples) | `details` (examples) | `text` |
|---|---|---|---|---|
| `turn.completed` | `answered` | `null` | `{ "provenance": [...], "confidence": "high\|low", "limitations": [...], "next_steps": [...] }` | final answer text |
| `turn.rejected` | `rejected` | moderation category: `unsafe_illegal`, `invalid_non_agricultural`, `political_controversial`, `cultural_sensitive`, `role_obfuscation`, … | `{ "category": "...", "policy": "..." }` | localized decline text |
| `turn.no_match` | `no_match` | `no_capability` \| `insufficient_result` | `{ "attempted_capabilities": [...] }` | user-facing no-match message |
| `turn.clarification` | `needs_clarification` | `ambiguous` | `{ "missing": ["district", "crop"] }` | the clarifying question |
| `turn.error` | `error` | `provider_unavailable` \| `timeout` \| `internal` | `{ "retryable": true }` | user-facing error message |

**`details` on an answered Turn** carries `provenance`, `confidence`, `limitations`,
`next_steps`. **[decided]** `confidence` is a coarse enum `{ high, low }` for v1 (finer
categories deferred, §8.1). `provenance[]` entries are **references only** —
`{ capability, source, url? }` — never response content or PII (§6.3). `limitations[]` and
`next_steps[]` are optional free-text arrays.

**`cause` is a closed, versioned enum. [decided]** Published with the contract and governed
by the DPG. Callers MUST treat an **unrecognised `cause` as the generic case of its
`status`**, so adding a code is a non-breaking change. `rejected` causes are the moderation
model's category enum verbatim (`valid_agricultural`, `invalid_non_agricultural`,
`invalid_external_reference`, `invalid_compound_mixed`, `invalid_language`, `unsafe_illegal`,
`political_controversial`, `cultural_sensitive`, `role_obfuscation`). Legacy muxed the
decline as a text chunk; here it is a typed terminal event, and a client that renders only
`content.delta` shows nothing spurious.

### 5.3 Non-streaming (`POST /v1/turns`)

Returns the single terminal object (§5.2 schema) with the full answer in `text`:

```jsonc
{ "status": "answered", "cause": null,
  "details": { "provenance": [...], "confidence": "high", "limitations": [], "next_steps": [] },
  "text": "<full answer text>" }
```

---

## 6. What stays DSS-internal (never on the wire)

History persistence (Redis `{session_id}_SVA`), suggestions generation, telemetry &
Langfuse tracing, LLM tier / route / fallback selection, translation batching &
sentence segmentation, `<think>`-block stripping, NPSS post-processing, disconnect-safe
finalization. Callers see only the headers + envelope in (§2.1, §4) and the event stream
out (§5).

Operational evidence (§6.3) — stage, selected capability, policy / moderation / routing /
review outcome, latency, terminal outcome — is emitted to the evidence sink **without
request/response content**, keyed by the per-Turn `trace_id` (which is the interaction
identifier of §6.3).

---

## 7. Per-repo migration notes

- **amul** → drop `use_translation_pipeline` / `pipeline_profile` (tenant config now);
  `phone`-driven farmer context resolves behind `subject_ref.ref`; `response_max_chars`
  moves to `request_options`; `session_id` → `X-Session-Id` header.
- **bharat** → `qid` → `X-Trace-Id` header; `session_id` → `X-Session-Id` header;
  `is_image_analysis` → `modality:"image"` on the image endpoint; `latitude`/`longitude`
  → `multipart` form fields on `/v1/turns:analyze-image`; rich JWT stays at Experience layer.
- **mh** → add explicit `channel`; `session_id` → `X-Session-Id` header;
  `farmer_id`/`unique_id` no longer on the envelope — resolved via `subject_ref.ref`.

---

## 8. Open points

### 8.1 Carried from architecture §8.3 (DSS-scoped, deferred to v1 design)

- **Minimum DSS contracts** — request, response, tool, context, evidence, error —
  including the **plan schema** (§5.5), since the plan is a first-class artifact policy
  evaluates and evidence records.
- **`TurnHistoryEntry` exact fields.** Direction **decided** — a framework-neutral schema
  (roles, content), not the orchestration framework's message type. Exact fields
  (tool-call trace inclusion, redaction posture) still to be designed.
- **`subject_ref` extensibility / `user_context`** — whether tenant profile fields attach
  through the opaque `ref` resolution only, or whether a non-personal `user_context`
  projected by Context Providers is also needed. Blocked on the Network Consumer Adapter
  resolution design (§3).
- **Translation boundary and personal-data classification** — which operating mode
  (translate-then-reason vs reason-in-native, §5.1) a tenant runs, and what that makes
  the DSS a processor of.
- **DSS-internal caches vs user context** — intent/selection caches (§5.2, §5.4) must
  stay a distinct store from Experience-owned history/profile; boundary enforcement open.
- **Finer confidence categories & human-escalation** — v1 uses `{ high, low }`; a richer
  scale and escalation surfacing in a terminal event are deferred.

### 8.2 PII passing through `query` and `history` — posture decided, residuals open

**Decision:** the **Experience API scrubs** PII from `query` and `history` before the DSS
sees them; the DSS treats them as clean, and sink-layer redaction (§6.2) is
defense-in-depth only. This keeps Posture A intact and the DSS out of the personal-data
processor classification. Residual open items:

- **Scrub quality.** Naive deletion removes tokens the query actually needs (a village
  name that *is* the mandi-lookup key). Preferred mitigation is **entity-preserving
  pseudonymization** at the Experience layer, not deletion — to be specified.
- **`history` is also untrusted input** to enrichment (§3.0): prior turns are data, never
  instructions.
- **Per-Provider forwarding allowlists** (which fields flow to which capability) — may
  still be needed alongside sink redaction (§8.3 open Q3).
- **Personalisation** ("Hi Ramesh…") when the DSS cannot see the name — templated response
  with post-DSS substitution, or opaque user-segment tokens (§8.3 open Q4).

### 8.3 Contract-specific open points

- **Exact `provenance` entry schema** inside `details` (fields beyond `capability` /
  `source` / `url`).
- **`cause` registry contents.** The enum shape is **decided** (closed, versioned,
  forward-compat unknown handling, §5.2); the exhaustive code list is still to be
  published per `status`.
- **Image-turn envelope** — how `subject_ref`, `request_options`, and `latitude`/
  `longitude` are encoded as `multipart/form-data` fields alongside the `image` part.

---

## 9. Worked examples (curl)

Illustrative only — host, ids, and text are placeholders. SSE responses show the raw
`event:`/`data:` frames the server writes; `--no-buffer` (`-N`) keeps them unbuffered.
Every processed Turn returns HTTP `200` regardless of outcome (§2.6).

### 9.1 Streaming — success (`turn.completed`)

**Request**

```bash
curl -N -X POST https://dss.example.internal/v1/turns:stream \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: sess_8f3a1c" \
  -H "X-Trace-Id: trc_9f2b7c1a" \
  -d '{
    "query": "What is the mandi price of wheat in my district this week?",
    "source_lang": "hi",
    "target_lang": "hi",
    "channel": "web",
    "subject_ref": {
      "user_id": "usr_analytics_9921",
      "ref": "prov_opaque_7b2f...e0",
      "issuer": "experience-api",
      "expires_at": "2026-08-24T18:30:00Z"
    },
    "history": [],
    "request_options": { "modality": "text", "response_max_chars": 1200, "stream": true }
  }'
```

**Response** — `200 OK`, `Content-Type: text/event-stream`, headers echo
`X-Session-Id`, `X-Trace-Id: trc_9f2b7c1a`

```
event: content.delta
data: {"text": "इस सप्ताह आपके जिले में गेहूं का मंडी भाव "}

event: content.delta
data: {"text": "लगभग ₹2,275 प्रति क्विंटल है।"}

event: turn.completed
data: {"status":"answered","cause":null,"details":{"provenance":[{"capability":"mandi_price_lookup","source":"Agmarknet","url":"https://agmarknet.gov.in/..."}],"confidence":"high","limitations":[],"next_steps":[]},"text":"इस सप्ताह आपके जिले में गेहूं का मंडी भाव लगभग ₹2,275 प्रति क्विंटल है।"}
```

### 9.2 Streaming — failure (`turn.rejected`, moderation)

A non-`answered` terminal can arrive with **zero** preceding `content.delta`. Note
`X-Trace-Id` is omitted here — the DSS mints one and returns it (§2.1).

**Request**

```bash
curl -N -X POST https://dss.example.internal/v1/turns:stream \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: sess_8f3a1c" \
  -d '{
    "query": "How do I make an explosive at home?",
    "source_lang": "en",
    "target_lang": "en",
    "channel": "web",
    "subject_ref": { "user_id": "anonymous", "ref": null, "issuer": "experience-api", "expires_at": null },
    "history": [],
    "request_options": { "modality": "text", "stream": true }
  }'
```

**Response** — `200 OK`, `text/event-stream`, `X-Trace-Id: trc_5d1e88af` (minted). The Turn
is handled; the *content* is refused.

```
event: turn.rejected
data: {"status":"rejected","cause":"unsafe_illegal","details":{"category":"unsafe_illegal","policy":"moderation.safety"},"text":"I can only answer agriculture and livestock related questions."}
```

Other failure terminals use the identical four-field schema, e.g. a provider outage:

```
event: turn.error
data: {"status":"error","cause":"provider_unavailable","details":{"retryable":true},"text":"I am unable to process your request right now. Please try again later."}
```

### 9.3 Non-streaming — success (`POST /v1/turns`)

**Request**

```bash
curl -X POST https://dss.example.internal/v1/turns \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: sess_8f3a1c" \
  -H "X-Trace-Id: trc_c4a01b2d" \
  -d '{
    "query": "What is the mandi price of wheat in my district this week?",
    "source_lang": "hi", "target_lang": "hi", "channel": "web",
    "subject_ref": { "user_id": "usr_analytics_9921", "ref": "prov_opaque_7b2f...e0", "issuer": "experience-api", "expires_at": "2026-08-24T18:30:00Z" },
    "history": [],
    "request_options": { "modality": "text", "stream": false }
  }'
```

**Response** — `200 OK`, `Content-Type: application/json`

```json
{
  "status": "answered",
  "cause": null,
  "details": {
    "provenance": [
      { "capability": "mandi_price_lookup", "source": "Agmarknet", "url": "https://agmarknet.gov.in/..." }
    ],
    "confidence": "high",
    "limitations": [],
    "next_steps": []
  },
  "text": "इस सप्ताह आपके जिले में गेहूं का मंडी भाव लगभग ₹2,275 प्रति क्विंटल है।"
}
```

### 9.4 Non-streaming — failure (`no_match`)

**Request**

```bash
curl -X POST https://dss.example.internal/v1/turns \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: sess_8f3a1c" \
  -H "X-Trace-Id: trc_77aa12ff" \
  -d '{
    "query": "Book me a flight to Paris",
    "source_lang": "en", "target_lang": "en", "channel": "web",
    "subject_ref": { "user_id": "anonymous", "ref": null, "issuer": "experience-api", "expires_at": null },
    "history": [],
    "request_options": { "modality": "text", "stream": false }
  }'
```

**Response** — `200 OK`, `application/json` (the Turn ran; no capability fit — §7 no-match,
not a 4xx; see §2.6)

```json
{
  "status": "no_match",
  "cause": "no_capability",
  "details": { "attempted_capabilities": [] },
  "text": "I couldn't find a way to help with that. I can assist with agriculture and livestock questions."
}
```

---

## 10. Decision log (v1 contract grill)

| # | Decision | Choice | Where |
|---|---|---|---|
| Q1 | Transport & protocol | REST + SSE | §2 (rationale below) |
| Q2 | PII scrub responsibility (query/history) | Experience API scrubs; DSS sink-redaction is defense-in-depth | §3, §8.2 |
| Q3/Q6 | `subject_ref` semantics | Pin "opaque to DSS"; resolution deferred to Network Consumer Adapter | §3 |
| Q4 | `trace_id` vs `interaction_id` | Unified — one per-Turn id | §2.1 (glossary below) |
| Q5 | Terminal metadata field name | `failure_details` → `details` | §5.2 |
| Q7 | `trace_id` safety | Must be non-personal; DSS mints if absent | §2.1 |
| Q8 | HTTP status convention | `200` for processed Turns; non-2xx only transport/validation | §2.6 |
| Q9 | API versioning | URI path (`/v1`); additive = no bump | §2.2 |
| Q10 | `history` wire type | Neutral `TurnHistoryEntry`, not framework `ModelMessage` | §4, §8.1 |
| Q11 | Service-to-service auth | None — network-isolation trust boundary | §2.3 |
| Q12 | Idempotency | `trace_id` as key; best-effort dedup | §2.4 |
| Q13 | SSE reconnection | Non-resumable + server-side finalization | §2.5 |
| Q14 | `cause` registry | Closed, versioned, forward-compat unknown handling | §5.2 |
| Q15 | `details` on answered | `confidence ∈ {high,low}`; provenance = references only | §5.2 |

### Q1 rationale — REST + SSE transport

**Context.** Architecture §8.3 left the DSS interface shape (REST / gRPC / in-process)
open, and the `entrypoint/` package is a scaffold pending it. The `UserTurn` contract
cannot be finalised without fixing how a caller reaches the DSS and how the streamed
answer returns. Load-bearing constraints: the DSS ships as a **separate immutable
container** in the adopter's network (§5.3), so a network boundary exists by construction;
the Turn is **streaming by nature** (token deltas, then one terminal event); adopter teams
are **polyglot** and already run the legacy SSE-over-HTTP endpoints.

**Decision.** REST over HTTP with SSE for the streamed response (endpoints in §2).

**Rejected alternatives.**
- *gRPC server-streaming* — strongly typed and efficient, but forces protobuf toolchains
  on every adopter for little in-network gain and fits browser/webhook clients poorly.
  Revisitable if a measured latency/throughput need emerges.
- *In-process library call* — lowest latency and no serialization, but contradicts the
  separate-container deployment model (§5.3), where the DSS and Experience API are
  independently deployed and scaled.

**Consequence.** SSE is one-directional after the initial POST — sufficient for a Turn,
which takes no further client input once started. A future bidirectional need (voice
barge-in) would reopen this.

### Q4 glossary — one per-Turn identifier

`trace_id` and `interaction_id` are the **same thing**: a single per-Turn correlation
identifier, caller-supplied via `X-Trace-Id` (the legacy `qid`), echoed back on the
response, and used as the operational-evidence key (§6). It must be an **opaque,
non-personal** token; the DSS mints one when the caller omits it, so evidence never keys
on caller PII. It is **distinct from `session_id`**, which spans many Turns. The contract
uses `trace_id` (header `X-Trace-Id`) throughout — avoid the synonyms `interaction_id`,
`correlation_id`, `qid`, `request_id`.
