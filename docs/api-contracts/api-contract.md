# DSS API Contract — `UserTurn`

**Status:** Draft (v1). **Audience:** the Experience API of a participating deployment
calling the DSS.

This contract defines a single turn interface — one request envelope in, one event stream
out — that supersedes the per-tenant `stream_chat_messages(...)` signatures across the amul,
bharat, and mh deployments. It is authoritative for the wire surface; for boundary and
posture rationale see `docs/DSS_ARCHITECTURE.md` (§8 there wins on any conflict). Settled
design decisions are listed in §10; unresolved items in §8.

---

## 1. Shape of the interface

One turn in, a stream of claims out, then exactly one terminal event.

| | |
|---|---|
| **In** | a request envelope — query, languages, channel, identity, optional location, history (§4) |
| **Out** | `claim` events, then one terminal event (§5) |
| **Owns** | interpreting the query and answering it |
| **Does not own** | authentication, sessions, history persistence, rate limits per user — all Experience's (§2.3) |

The DSS stores nothing that outlives a turn, except its own evidence (§6).

Legacy per-tenant signatures this replaces are mapped in §7.

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

Both identifiers are echoed on the response. `trace_id` is **also** in the terminal event
body (§5.2) — a header is lost the moment Experience stores or forwards a turn, and the audit
trail needs the join key to survive alongside the stored answer.

No CORS headers are set. Only the Experience API reaches this port (§2.3); a browser never
does.

There is exactly one per-turn identifier — see the identifier model in §10.

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

### 2.4 Idempotency — not in v1

**Replay protection is deferred.** `X-Trace-Id` is a correlation key only. A turn re-sent
with the same `trace_id` re-executes, including any side-effecting tool it reaches.

The gap is real: a retrying caller can invoke a Provider capability twice. Two things are
needed before it can be closed, and neither exists — a caller-supplied request id that
survives a retry (`X-Trace-Id` is optional and minted when absent, so it does not), and a
store of prior outcomes. Compensation across committed Provider side effects is a separate
problem again (§8).

Until then, callers must treat a failed turn as possibly-executed.

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
| `429` | Global concurrency cap reached. Carries `Retry-After`. Not a per-user quota — those belong to Experience (§2.3). |
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
  "source_lang": "string",              // BCP 47. inbound language; drives routing and skill filtering
  "target_lang": "string",              // BCP 47. outbound language; drives composition and review
  "channel":     "web|whatsapp|voice|sms",

  "subject_ref": {                      // identity, opaque (§3)
    "user_id":    "string",
    "ref":        "string|null",
    "issuer":     "string|null",
    "expires_at": "string|null"
  },

  "location": {                         // optional — drives coverage-area filtering
    "district": "string|null",
    "state":    "string|null",
    "lat":      0.0,
    "lon":      0.0
  },

  "history": [ /* TurnHistoryEntry[]: framework-neutral { role, content, … };
                  not the orchestration framework's message type. Fields deferred (§8). */ ],

  "request_options": {                  // all optional
    "modality":           "text|image",
    "response_max_chars": 0
  }
}
```

Rules:

- `query`, `source_lang`, `target_lang`, and `channel` are required; `X-Session-Id` is
  required in headers.
- `source_lang` and `target_lang` are BCP 47 tags validated against a supported set the DSS
  publishes. The set is configuration, not part of this contract — adding a language is not a
  contract change.
- `subject_ref.ref` is required for capability-backed turns.
- `location` is optional with no default. Provider Discovery filters coverage areas on it
  when present. Two of three deployments cannot fill it.
- `location.lat`/`lon` are expected pre-rounded by the caller. Whether rounding is required,
  and to what precision, sits with the Experience layer.
- `history` may be empty. Entries use the neutral `TurnHistoryEntry` schema; the DSS maps
  them to its internal representation at the boundary and never writes to the caller's
  store.
- **Whether a turn streams follows from the endpoint, not a flag.** `/v1/turns:stream`
  streams; `/v1/turns` does not. There is no `stream` option — offering one would let a
  caller ask for an invalid combination.
- `response_max_chars` is a caller-supplied cap. The Experience layer holds the per-channel
  value; the DSS honours whatever it is given.
- Unrecognized fields are rejected, including raw identity objects, framework handles, LLM
  tier flags, and `session_id`/`qid` in the body.

---

## 5. Response

Claims stream, followed by exactly one terminal event. Only an answered turn streams —
`rejected`, `no_match`, and `needs_clarification` send the terminal event alone.

### 5.1 Claim events

A claim is **one whole sentence**, complete when emitted. Not a character delta. The caller
groups claims into what its channel can send — a speakable phrase for voice, a paragraph for
chat.

```
event: claim
data: {"text": "Potato grows best in well-drained sandy loam soil.", "source_id": "1"}

event: claim
data: {"text": "Keep soil pH between 5.2 and 6.4.", "source_id": "1"}
```

`source_id` is `null` for a connective sentence that cites nothing.

### 5.2 Terminal event

One flat schema for every outcome. Fields not relevant to an outcome are empty, never
absent.

```jsonc
{
  "status":      "answered|rejected|no_match|needs_clarification|error",
  "cause":       "string|null",     // ReasonCode; null when answered
  "text":        "string",          // the full answer, or the refusal message
  "sources":     [ { "id": "1", "name": "Agmarknet", "kind": "provider", "url": "string|null" } ],
  "confidence":  "high|low",
  "limitations": [ "string" ],
  "refused":     [ { "what": "gold prices", "reason": "outside agriculture" } ],
  "missing":     [ { "name": "market.state" } ],
  "trace_id":    "string"
}
```

| Field | When it carries data |
|---|---|
| `sources` | any turn that cited something |
| `refused[]` | part of the question was out of scope; the rest may still be answered |
| `missing[]` | an input could not be filled — the caller should ask the farmer |
| `limitations[]` | caveats on the answer |
| `trace_id` | always — in the body, not only the header, so it survives being stored |

**`refused` and `missing` can both appear on an `answered` turn.** A farmer asking three
things may get two answers, one refusal, and one follow-up question. Status reflects whether
*anything* was answered:

| Answered anything | `missing` | `status` |
|:--:|:--:|---|
| yes | empty | `answered` |
| yes | some | `answered` — with `missing[]` set |
| no | some | `needs_clarification` |
| no | empty | `no_match` |

**`text` is authoritative.** On a streaming turn it repeats the assembled claims, so a
caller that renders claims live must not also append `text`. It exists for the
non-streaming case and for storage.

**No capability names on the wire.** `sources` carries what the farmer sees — the Provider
or document name. Which capability produced a result is internal and goes to telemetry keyed
by `trace_id` (§6).

`cause` is a closed, versioned enum published with the contract. Callers must treat an
unrecognized `cause` as the generic case of its `status`, so adding a code is non-breaking.

| Group | Codes |
|---|---|
| harm — reads the query only | `unsafe_illegal`, `role_obfuscation`, `political_controversial`, `external_reference`, `adopter_policy` |
| scope — reads the intent | `domain_unmapped`, `intent_low_confidence`, `unsupported_action_type` |
| infrastructure | `unavailable`, `provider_unavailable`, `timeout`, `internal` |

### 5.3 Stream faults

A dropped connection and a completed turn are distinguishable: a completed turn always ends
with a terminal event. If the DSS fails mid-stream it emits a terminal event with
`status: "error"` before closing. If the connection itself drops, no terminal event
arrives — the caller recovers per §2.5.

### 5.4 Non-streaming response

`POST /v1/turns` returns the same terminal object directly, with no claim events and the
full answer in `text`.

---

## 6. DSS-internal behavior

The following are not part of the wire contract: history persistence, suggestion
generation, telemetry and tracing, LLM tier and fallback selection, translation, thinking
removal, image post-processing, and disconnect finalization.

Evidence is emitted in **two tiers**, both keyed by the per-turn `trace_id`:

| Tier | Content | Retention | Access |
|---|---|---|---|
| Metrics | stage, selected capability, moderation and routing outcomes, latency, terminal outcome. No user content. | long | broad |
| Diagnostic | the above plus query, answer, and source list | short | restricted |

The diagnostic tier holds farmer content deliberately — metadata alone tells you *that*
something changed, not *what went wrong*. Retention, masking before write, and who may query
it are open (§8).

---

## 7. Migration notes

The DSS replaces `app/services/chat.py::stream_chat_messages` in the amul, bharat, and mh
deployments. Their unioned parameters map as follows.

| Legacy parameter | amul | bharat | mh | Disposition |
|---|:--:|:--:|:--:|---|
| `query` | ✅ | ✅ | ✅ | Body field `query` |
| `session_id` | ✅ | ✅ | ✅ | Header `X-Session-Id` (§2.1) |
| `source_lang` | ✅ | ✅ | ✅ | Body field `source_lang` |
| `target_lang` | ✅ | ✅ | ✅ | Body field `target_lang` |
| `user_id` | ✅ | ✅ | ✅ | Moved into `subject_ref` (§3) |
| `history` | ✅ | ✅ | ✅ | Body field `history`; read-only to the DSS |
| `user_info` / `current_user` | ✅ | ✅ | ✅ | Dropped; replaced by `subject_ref` (§3) |
| `channel` | ✅ | ✅ | ❌ | Body field `channel` |
| `background_tasks` | ✅ | ✅ | ✅ | Dropped; DSS owns its own async work |
| `use_translation_pipeline` | ✅ | ❌ | ❌ | Dropped; tenant/Identity config, not a caller flag |
| `pipeline_profile` | ✅ | ❌ | ❌ | Dropped; LLM tier selection is DSS-internal |
| `qid` | ❌ | ✅ | ❌ | Header `X-Trace-Id` (§2.1) |
| `is_image_analysis` | ❌ | ✅ | ❌ | `request_options.modality = "image"` (§4) |
| `latitude` / `longitude` | ❌ | ✅ | ❌ | Body field `location` (§4) |

The legacy functions returned an `AsyncGenerator[str, None]` — a plain-text stream with
declines and errors muxed inline, and moderation, provenance, and confidence visible only in
internal tracing. This contract surfaces that structure as typed claim and terminal events
(§5).

Per deployment:

- **amul** — drop `use_translation_pipeline` and `pipeline_profile`; move phone-driven
  farmer context behind `subject_ref.ref`; move `response_max_chars` to `request_options`;
  move `session_id` to `X-Session-Id`.
- **bharat** — `qid` → `X-Trace-Id`; `session_id` → `X-Session-Id`; `is_image_analysis` →
  `modality: "image"`; `latitude`/`longitude` → the `location` object (§4);
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
- **`cause` registry contents.** Enum shape is fixed; the full code list per `status` is to
  be published.
- **Replay protection.** A caller-supplied request id that survives a retry, plus a store of
  prior outcomes. Deferred (§2.4); until then a failed turn may already have executed.
- **Telemetry retention and access.** The diagnostic tier holds farmer content (§6). Open:
  retention window, masking before write, who may query it, and whether reads are audited.
- **Image turns.** No design work exists for the image flow beyond the endpoint and
  `modality` flag. Whether the DSS handles images at all is unsettled.
- **Stream heartbeat.** A voice turn is silent through intent, moderation, discovery and
  planning. Infrastructure closes idle connections — Cloudflare caps SSE at 30 seconds, AWS
  ALB closes idle by default. Whether to emit a keep-alive, or to treat that latency as an
  SLA failure instead, is undecided.
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
    "location": { "district": "Anand", "state": "Gujarat", "lat": 22.56, "lon": 72.93 },
    "history": [],
    "request_options": { "modality": "text", "response_max_chars": 1200 }
  }'
```

```
event: claim
data: {"text":"इस सप्ताह आनंद मंडी में गेहूं का भाव ₹2,275 प्रति क्विंटल है।","source_id":"1"}

event: turn.completed
data: {"status":"answered","cause":null,"text":"इस सप्ताह आनंद मंडी में गेहूं का भाव ₹2,275 प्रति क्विंटल है।","sources":[{"id":"1","name":"Agmarknet","kind":"provider","url":"https://agmarknet.gov.in/..."}],"confidence":"high","limitations":[],"refused":[],"missing":[],"trace_id":"trc_9f2b7c1a"}
```

### 9.2 Streaming — rejected

`X-Trace-Id` is omitted; the DSS mints one and returns it. A rejected turn does not stream —
the terminal event arrives with no preceding claim.

```bash
curl -N -X POST https://dss.example.internal/v1/turns:stream \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: sess_8f3a1c" \
  -d '{
    "query": "How do I make an explosive at home?",
    "source_lang": "en", "target_lang": "en", "channel": "web",
    "subject_ref": { "user_id": "anonymous", "ref": null, "issuer": "experience-api", "expires_at": null },
    "history": [],
    "request_options": { "modality": "text" }
  }'
```

```
event: turn.rejected
data: {"status":"rejected","cause":"unsafe_illegal","text":"I can only answer agriculture and livestock related questions.","sources":[],"confidence":"high","limitations":[],"refused":[{"what":"making explosives","reason":"unsafe"}],"missing":[],"trace_id":"trc_minted_4a1f"}
```

A provider outage uses the same schema:

```
event: turn.error
data: {"status":"error","cause":"provider_unavailable","text":"I am unable to process your request right now. Please try again later.","sources":[],"confidence":"low","limitations":[],"refused":[],"missing":[],"trace_id":"trc_minted_4a1f"}
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
    "location": { "district": "Anand", "state": "Gujarat", "lat": 22.56, "lon": 72.93 },
    "history": [],
    "request_options": { "modality": "text" }
  }'
```

```json
{
  "status": "answered",
  "cause": null,
  "text": "इस सप्ताह आनंद मंडी में गेहूं का भाव ₹2,275 प्रति क्विंटल है।",
  "sources": [ { "id": "1", "name": "Agmarknet", "kind": "provider", "url": "https://agmarknet.gov.in/..." } ],
  "confidence": "high",
  "limitations": [],
  "refused": [],
  "missing": [],
  "trace_id": "trc_c4a01b2d"
}
```

### 9.4 Non-streaming — no match

```json
{
  "status": "no_match",
  "cause": "domain_unmapped",
  "text": "I couldn't find a way to help with that. I can assist with agriculture and livestock questions.",
  "sources": [],
  "confidence": "low",
  "limitations": [],
  "refused": [],
  "missing": [],
  "trace_id": "trc_a17c3e90"
}
```

### 9.5 The partial case — answered, with a refusal and a follow-up

*"Which soil is best for potato? What is the price of potato and gold?"* — two asks served,
gold refused, and the mandi lookup missing a district.

```json
{
  "status": "answered",
  "cause": null,
  "text": "Potato grows best in well-drained sandy loam soil. Keep soil pH between 5.2 and 6.4. I cannot help with gold prices. Which district should I check potato prices for?",
  "sources": [ { "id": "1", "name": "ICAR Potato Guide", "kind": "document", "url": "https://icar.example/..." } ],
  "confidence": "high",
  "limitations": [],
  "refused": [ { "what": "gold prices", "reason": "outside agriculture" } ],
  "missing": [ { "name": "market.state" } ],
  "trace_id": "trc_5b8d2f11"
}
```

`status` is `answered` because something was answered. `missing[]` tells the caller a
follow-up is needed; `refused[]` tells it part of the question was out of scope.

---

## 10. Design decisions

| Decision | Choice | Reference |
|---|---|---|
| Transport | REST + SSE | §2, below |
| PII scrubbing | Experience API scrubs `query`/`history`; DSS redaction is defense-in-depth | §3 |
| `subject_ref` semantics | Opaque to the DSS; resolution deferred | §3 |
| Identifier model | `trace_id` and interaction id unified | §2.1, below |
| Streaming unit | **claims** — one whole sentence each, with `source_id` | §5.1 |
| Terminal schema | **flat.** Reverses the earlier `details{}` wrapper | §5.2 |
| Capability names on the wire | **none.** `sources` carries farmer-visible names only; capability goes to telemetry | §5.2, §6 |
| Evidence content | **two tiers.** Reverses the earlier "no request or response content" | §6 |
| Trace-id placement | header **and** terminal body, so it survives storage | §2.1, §5.2 |
| Trace-id safety | Opaque, non-personal; DSS mints when absent | §2.1 |
| HTTP status | `200` for processed turns; non-2xx for transport/validation only | §2.6 |
| Versioning | URI path; additive changes do not bump | §2.2 |
| `history` wire type | Neutral `TurnHistoryEntry` | §4 |
| Service authentication | None; network-perimeter trust | §2.3 |
| Idempotency | **deferred.** `trace_id` is correlation only; replays re-execute | §2.4 |
| Who decides streaming | the endpoint, not a caller flag | §4 |
| Language set | BCP 47, validated against published config, not enumerated here | §4 |
| Reconnection | Non-resumable; server-side finalization | §2.5 |
| `cause` registry | Closed, versioned, forward-compatible | §5.2 |
| Answered fields | `confidence ∈ {high, low}`; `refused[]` and `missing[]` may both appear | §5.2 |

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
