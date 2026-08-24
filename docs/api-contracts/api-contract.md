# DSS API Contract — `UserTurn`

**Status:** Draft (v1). **Audience:** the Experience API of a participating deployment
calling the DSS.

This contract defines a single turn interface — one request envelope in, one event stream
out — that supersedes the per-tenant `stream_chat_messages(...)` signatures across the amul,
bharat, and mh deployments. It is authoritative for the wire surface; for boundary and
posture rationale see `docs/DSS_ARCHITECTURE.md` (§8 there wins on any conflict). Settled
design decisions are listed in §10; unresolved items in §8.

---

## 1. Legacy mapping

The DSS replaces `app/services/chat.py::stream_chat_messages` in all three repos. Their
unioned parameters map as follows.

| Legacy parameter | amul | bharat | mh | Disposition |
|---|:--:|:--:|:--:|---|
| `query` | ✅ | ✅ | ✅ | Body field `query` |
| `session_id` | ✅ | ✅ | ✅ | Header `X-Session-Id` (§2.1) |
| `source_lang` | ✅ | ✅ | ✅ | Body field `source_lang` |
| `target_lang` | ✅ | ✅ | ✅ | Body field `target_lang` |
| `user_id` | ✅ | ✅ | ✅ | Moved into `subject_ref` (§3) |
| `history` | ✅ | ✅ | ✅ | Body field `history` (`TurnHistoryEntry[]`); read-only to the DSS |
| `user_info` / `current_user` | ✅ | ✅ | ✅ | Dropped; replaced by `subject_ref` (§3) |
| `channel` | ✅ | ✅ | ❌ | Body field `channel` |
| `background_tasks` | ✅ | ✅ | ✅ | Dropped; DSS owns its own async work |
| `use_translation_pipeline` | ✅ | ❌ | ❌ | Dropped; tenant/Identity config, not a caller flag |
| `pipeline_profile` | ✅ | ❌ | ❌ | Dropped; LLM tier selection is DSS-internal |
| `qid` | ❌ | ✅ | ❌ | Header `X-Trace-Id` (§2.1) |
| `is_image_analysis` | ❌ | ✅ | ❌ | `request_options.modality = "image"` (§4) |
| `latitude` / `longitude` | ❌ | ✅ | ❌ | `multipart` fields on `/v1/turns:analyze-image` (§8) |

The legacy functions returned an `AsyncGenerator[str, None]` — a plain-text event stream
with declines and errors muxed inline, and moderation, provenance, and confidence exposed
only to internal tracing. This contract surfaces that structure as typed terminal events
(§5).

---

## 2. Transport

REST over HTTP, with Server-Sent Events (SSE) for streaming responses.

| Operation | Method and path | Request body | Response |
|---|---|---|---|
| Streaming turn | `POST /v1/turns:stream` | `application/json` (§4) | `text/event-stream` (§5) |
| Image turn | `POST /v1/turns:analyze-image` | `multipart/form-data` | `text/event-stream` (§5) |
| Non-streaming turn | `POST /v1/turns` | `application/json` | `application/json` (§5.3) |

Requests use `POST` because `history` and context payloads do not belong in a query
string and the image flow requires `multipart`.

### 2.1 Headers

Conversation and correlation identifiers travel in headers.

| Header | Required | Description |
|---|:--:|---|
| `X-Session-Id` | Yes | Conversation key. The DSS reads `history` under it; the Experience API owns persistence. |
| `X-Trace-Id` | No | Per-turn correlation id. Must be opaque and non-personal; the DSS mints one when absent. Serves as the idempotency key (§2.4) and operational-evidence key (§6). |
| `Content-Type` | Yes | `application/json` or `multipart/form-data`. |

Both identifiers are echoed on the response
(`Access-Control-Expose-Headers: X-Session-Id, X-Trace-Id`). There is exactly one per-turn
identifier — see the identifier model in §10.

### 2.2 Versioning

The API version is carried in the URI path (`/v1/...`). Additive, backward-compatible
changes do not bump the version; a breaking change to the envelope or terminal schema
introduces `/v2`.

### 2.3 Authentication and trust boundary

The DSS performs no application-level authentication of either the end user or the calling
service; its trust boundary is the network perimeter. The deployment must ensure that only
the Experience API can reach the DSS port (network policy or service mesh). Accordingly:

- The DSS never issues `401` or `403`; caller and user authentication belong to the
  Experience edge.
- Rate limiting is owned by the Experience edge. The DSS may apply a global concurrency
  cap but enforces no per-user quotas.

### 2.4 Idempotency

`X-Trace-Id` is the idempotency key. A turn replayed with a `trace_id` seen within a short
window returns the prior terminal outcome rather than re-executing side-effecting tools.
This is best-effort deduplication, not distributed exactly-once; compensation across
committed Provider side effects is out of scope (§8).

### 2.5 Streaming and reconnection

The SSE stream is non-resumable in v1. If the connection drops mid-turn, the DSS finalizes
the turn server-side and persists the answer to the session. The client recovers by
re-issuing with the same `X-Session-Id` — the completed turn is then present in history.
`Last-Event-ID` replay is deferred.

### 2.6 HTTP status codes

A turn the DSS processed returns `200` regardless of outcome; `rejected`, `no_match`, and
`error` are carried in the terminal event's `status` (§5.2), not the HTTP code. Non-2xx
codes are reserved for transport and validation faults:

| Code | Meaning |
|---|---|
| `400` | Malformed request (unparseable body, missing required header) |
| `422` | Schema violation (well-formed but invalid field) |
| `503` | DSS unavailable or overloaded |

---

## 3. Identity and PII

Identity is carried as a single opaque `subject_ref` object minted by the Experience API.
The envelope carries no raw personal data — no phone, farmer ID, unique ID, name, email,
or JWT claims.

```jsonc
"subject_ref": {
  "user_id":    "string",        // analytics/correlation id only; "anonymous" if unknown
  "ref":        "string|null",   // Provider-scoped opaque token; the DSS transports but never
                                  //   inspects, resolves, persists, or logs it
  "issuer":     "string|null",   // who minted the ref, for audit
  "expires_at": "string|null"    // RFC3339 bound; the DSS treats an expired ref as absent
}
```

- The DSS never inspects, resolves, or validates `subject_ref`. What `ref` resolves to,
  and who validates it, are deferred to the Network Consumer Adapter design (§8).
- `ref` is required for any turn that invokes a Provider capability on the caller's behalf;
  `subject_ref` may otherwise be anonymous.
- Personal payloads a Provider requires travel the protected Experience→Provider path, not
  the prompt. The DSS does not resolve farmer context from a raw phone number.

Per-tenant effect: amul's phone-driven farmer lookup, bharat's JWT claims, and mh's
`farmer_id`/`unique_id` all move behind `ref` resolution or remain at the Experience layer;
none appear on the envelope.

**Free-text PII.** `subject_ref` removes structured PII only. `query` and `history` are
scrubbed by the Experience API before the DSS sees them; sink-layer redaction is
defense-in-depth. Residual items are tracked in §8.

---

## 4. Request envelope

Body of `POST /v1/turns:stream` (identifiers in headers, §2.1):

```jsonc
{
  "query":       "string",              // user utterance (PII-scrubbed by the caller)
  "source_lang": "gu|hi|mr|en|...",     // inbound language; drives routing and skill filtering
  "target_lang": "gu|hi|mr|en|...",     // outbound language; drives composition and review
  "channel":     "web|whatsapp|voice|sms|...",

  "subject_ref": {                      // identity, opaque (§3)
    "user_id":    "string",
    "ref":        "string|null",
    "issuer":     "string|null",
    "expires_at": "string|null"
  },

  "history": [ /* TurnHistoryEntry[]: framework-neutral { role, content, … };
                  not the orchestration framework's message type. Fields deferred (§8). */ ],

  "request_options": {                  // all optional
    "modality":           "text|image",
    "response_max_chars": 0,
    "stream":             true
  }
}
```

Rules:

- `query`, `source_lang`, `target_lang`, and `channel` are required; `X-Session-Id` is
  required in headers.
- `subject_ref.ref` is required for capability-backed turns.
- `history` may be empty. Entries use the neutral `TurnHistoryEntry` schema; the DSS maps
  them to its internal representation at the boundary and never writes to the caller's
  store.
- Unrecognized fields are rejected, including raw identity objects, framework handles, LLM
  tier flags, and `session_id`/`qid` in the body.

---

## 5. Response

The answer streams as plain text, followed by exactly one terminal event.

### 5.1 Content deltas

```
event: content.delta
data: {"text": "<partial answer text>"}     // repeated zero or more times
```

### 5.2 Terminal event

Every terminal event uses the same four-field schema. A non-`answered` terminal may arrive
with no preceding `content.delta`.

```jsonc
{
  "status":  "answered|rejected|no_match|needs_clarification|error",
  "cause":   "string|null",   // machine code; null when status = answered
  "details": { /* object */ },// outcome context (see table)
  "text":    "string"         // human-readable message; the final answer when answered
}
```

| Event | `status` | `cause` | `details` |
|---|---|---|---|
| `turn.completed` | `answered` | `null` | `{ provenance[], confidence, limitations[], next_steps[] }` |
| `turn.rejected` | `rejected` | moderation category | `{ category, policy }` |
| `turn.no_match` | `no_match` | `no_capability` \| `insufficient_result` | `{ attempted_capabilities[] }` |
| `turn.clarification` | `needs_clarification` | `ambiguous` | `{ missing[] }` |
| `turn.error` | `error` | `provider_unavailable` \| `timeout` \| `internal` | `{ retryable }` |

On an answered turn, `confidence` is `high` or `low` (a finer scale is deferred), and
`provenance[]` entries are references only — `{ capability, source, url? }` — never response
content or PII.

`cause` is a closed, versioned enum published with the contract. Callers must treat an
unrecognized `cause` as the generic case of its `status`, so adding a code is
non-breaking. `rejected` causes are the moderation model's categories: `valid_agricultural`,
`invalid_non_agricultural`, `invalid_external_reference`, `invalid_compound_mixed`,
`invalid_language`, `unsafe_illegal`, `political_controversial`, `cultural_sensitive`,
`role_obfuscation`.

### 5.3 Non-streaming response

`POST /v1/turns` returns the terminal object directly, with the full answer in `text`:

```jsonc
{
  "status":  "answered",
  "cause":   null,
  "details": { "provenance": [ … ], "confidence": "high", "limitations": [], "next_steps": [] },
  "text":    "<full answer text>"
}
```

---

## 6. DSS-internal behavior

The following are not part of the wire contract: history persistence, suggestion
generation, telemetry and tracing, LLM tier and fallback selection, translation, thinking
removal, image post-processing, and disconnect finalization.

Operational evidence — stage, selected capability, moderation and routing outcomes,
latency, terminal outcome — is emitted without request or response content, keyed by the
per-turn `trace_id`.

---

## 7. Migration notes

- **amul** — drop `use_translation_pipeline` and `pipeline_profile`; move phone-driven
  farmer context behind `subject_ref.ref`; move `response_max_chars` to `request_options`;
  move `session_id` to `X-Session-Id`.
- **bharat** — `qid` → `X-Trace-Id`; `session_id` → `X-Session-Id`; `is_image_analysis` →
  `modality: "image"`; `latitude`/`longitude` → multipart fields on the image endpoint;
  JWT claims remain at the Experience layer.
- **mh** — add an explicit `channel`; `session_id` → `X-Session-Id`; resolve
  `farmer_id`/`unique_id` via `subject_ref.ref`.

---

## 8. Open items

- **`subject_ref` resolution.** How `ref` resolves and who validates it, pending the
  Network Consumer Adapter design.
- **`subject_ref` extensibility.** Whether non-personal tenant fields need a `user_context`
  projection in addition to `ref`.
- **`TurnHistoryEntry` fields.** Neutral-schema direction is fixed; concrete fields
  (tool-call inclusion, redaction) are not.
- **`provenance` entry schema.** Fields beyond `capability`, `source`, and `url`.
- **`cause` registry contents.** Enum shape is fixed; the full code list per `status` is to
  be published.
- **Image-turn envelope.** Encoding of `subject_ref`, `request_options`, and coordinates as
  multipart fields.
- **Free-text PII.** Scrub quality (entity-preserving pseudonymization vs deletion),
  per-Provider forwarding allowlists, and personalization without visible names.
- **Confidence scale and escalation.** A finer scale and human-escalation surfacing.
- **Translation mode.** Per-tenant translate-then-reason vs reason-in-native selection.

---

## 9. Examples

Illustrative values only. SSE frames are shown as written by the server; every processed
turn returns HTTP `200` (§2.6).

### 9.1 Streaming — answered

```bash
curl -N -X POST https://dss.example.internal/v1/turns:stream \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: sess_8f3a1c" \
  -H "X-Trace-Id: trc_9f2b7c1a" \
  -d '{
    "query": "What is the mandi price of wheat in my district this week?",
    "source_lang": "hi", "target_lang": "hi", "channel": "web",
    "subject_ref": { "user_id": "usr_9921", "ref": "prov_opaque_7b2f...e0",
                     "issuer": "experience-api", "expires_at": "2026-08-24T18:30:00Z" },
    "history": [],
    "request_options": { "modality": "text", "response_max_chars": 1200, "stream": true }
  }'
```

```
event: content.delta
data: {"text": "इस सप्ताह आपके जिले में गेहूं का मंडी भाव "}

event: content.delta
data: {"text": "लगभग ₹2,275 प्रति क्विंटल है।"}

event: turn.completed
data: {"status":"answered","cause":null,"details":{"provenance":[{"capability":"mandi_price_lookup","source":"Agmarknet","url":"https://agmarknet.gov.in/..."}],"confidence":"high","limitations":[],"next_steps":[]},"text":"इस सप्ताह आपके जिले में गेहूं का मंडी भाव लगभग ₹2,275 प्रति क्विंटल है।"}
```

### 9.2 Streaming — rejected

`X-Trace-Id` is omitted; the DSS mints one and returns it. The terminal event carries no
preceding `content.delta`.

```bash
curl -N -X POST https://dss.example.internal/v1/turns:stream \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: sess_8f3a1c" \
  -d '{
    "query": "How do I make an explosive at home?",
    "source_lang": "en", "target_lang": "en", "channel": "web",
    "subject_ref": { "user_id": "anonymous", "ref": null, "issuer": "experience-api", "expires_at": null },
    "history": [],
    "request_options": { "modality": "text", "stream": true }
  }'
```

```
event: turn.rejected
data: {"status":"rejected","cause":"unsafe_illegal","details":{"category":"unsafe_illegal","policy":"moderation.safety"},"text":"I can only answer agriculture and livestock related questions."}
```

A provider outage uses the same schema:

```
event: turn.error
data: {"status":"error","cause":"provider_unavailable","details":{"retryable":true},"text":"I am unable to process your request right now. Please try again later."}
```

### 9.3 Non-streaming — answered

```bash
curl -X POST https://dss.example.internal/v1/turns \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: sess_8f3a1c" \
  -H "X-Trace-Id: trc_c4a01b2d" \
  -d '{
    "query": "What is the mandi price of wheat in my district this week?",
    "source_lang": "hi", "target_lang": "hi", "channel": "web",
    "subject_ref": { "user_id": "usr_9921", "ref": "prov_opaque_7b2f...e0",
                     "issuer": "experience-api", "expires_at": "2026-08-24T18:30:00Z" },
    "history": [],
    "request_options": { "modality": "text", "stream": false }
  }'
```

```json
{
  "status": "answered",
  "cause": null,
  "details": {
    "provenance": [ { "capability": "mandi_price_lookup", "source": "Agmarknet", "url": "https://agmarknet.gov.in/..." } ],
    "confidence": "high",
    "limitations": [],
    "next_steps": []
  },
  "text": "इस सप्ताह आपके जिले में गेहूं का मंडी भाव लगभग ₹2,275 प्रति क्विंटल है।"
}
```

### 9.4 Non-streaming — no match

```json
{
  "status": "no_match",
  "cause": "no_capability",
  "details": { "attempted_capabilities": [] },
  "text": "I couldn't find a way to help with that. I can assist with agriculture and livestock questions."
}
```

---

## 10. Design decisions

| Decision | Choice | Reference |
|---|---|---|
| Transport | REST + SSE | §2, below |
| PII scrubbing | Experience API scrubs `query`/`history`; DSS redaction is defense-in-depth | §3 |
| `subject_ref` semantics | Opaque to the DSS; resolution deferred | §3 |
| Identifier model | `trace_id` and interaction id unified | §2.1, below |
| Terminal metadata field | `details` (uniform across outcomes) | §5.2 |
| Trace-id safety | Opaque, non-personal; DSS mints when absent | §2.1 |
| HTTP status | `200` for processed turns; non-2xx for transport/validation only | §2.6 |
| Versioning | URI path; additive changes do not bump | §2.2 |
| `history` wire type | Neutral `TurnHistoryEntry` | §4 |
| Service authentication | None; network-perimeter trust | §2.3 |
| Idempotency | `trace_id` key; best-effort dedup | §2.4 |
| Reconnection | Non-resumable; server-side finalization | §2.5 |
| `cause` registry | Closed, versioned, forward-compatible | §5.2 |
| Answered `details` | `confidence ∈ {high, low}`; provenance references only | §5.2 |

### Transport rationale

The DSS ships as a separate container in the adopter's network, so a network boundary
exists by construction; a turn is inherently streaming; and adopter teams already operate
SSE-over-HTTP endpoints. REST + SSE meets these with no new tooling. gRPC server-streaming
was rejected as imposing protobuf toolchains for little in-network gain and a poor fit for
browser and webhook clients; an in-process call was rejected as incompatible with the
separate-container deployment model. SSE is one-directional after the initial `POST`, which
suffices for a turn; a future bidirectional need (voice barge-in) would reopen this.

### Identifier model

`trace_id` and the operational-evidence "interaction id" are one value: a per-turn
correlation id supplied via `X-Trace-Id` (formerly `qid`), echoed on the response, and used
as the evidence key. It must be opaque and non-personal, and the DSS mints one when the
caller omits it. It is distinct from `session_id`, which spans many turns. The contract uses
`trace_id` throughout; `interaction_id`, `correlation_id`, `qid`, and `request_id` are
avoided as synonyms.
