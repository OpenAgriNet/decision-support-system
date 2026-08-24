# DSS API Contract — `UserTurn` (chat / decision turn)

> Status: **Draft (v1 design)**. This contract normalizes the `stream_chat_messages`
> surface from the three OAN participating-deployment APIs (amul, bharat, mh) into a
> single DSS interface. Companion reading: `docs/DSS_ARCHITECTURE.md` §5.1 (request
> envelope), §2.1 (provides), §6.1 (PII posture), §7 (failure behaviour), §8.3 (open
> items). Where this contract and §8 disagree, §8 is the authority — flag drift in a PR.

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
| `history` | ✅ | ✅ | ✅ | **Kept** — `history` (body); DSS reads, never writes |
| `user_info` / `current_user` (JWT dict) | ✅ | ✅ | ✅ | **Dropped from wire** — see §3 (opaque ref) |
| `channel` | ✅ | ✅ (JWT) | ❌ | **Kept** — `channel` (body) |
| `background_tasks` | ✅ | ✅ | ✅ | **Dropped** — FastAPI internal; DSS owns its own async work |
| `use_translation_pipeline` | ✅ | ❌ | ❌ | **Dropped** — translation mode is tenant/Identity config (§5.1), not a caller flag |
| `pipeline_profile` | ✅ | ❌ | ❌ | **Dropped** — LLM tier selection is DSS-internal |
| `qid` | ❌ | ✅ | ❌ | **Moved to header** — `X-Trace-Id` (§2.1) |
| `is_image_analysis` | ❌ | ✅ | ❌ | **Kept** — `request_options.modality = "image"` + image endpoint |
| `latitude` / `longitude` | ❌ | ✅ | ❌ | **Kept (optional)** — `request_options.coordinates` |

**Output, all three:** `AsyncGenerator[str, None]` — a `text/event-stream` of plain
text chunks, with moderation declines and error messages muxed inline as text, and
moderation category / provenance / route / confidence computed internally but **never
surfaced to the caller** (they went only to Langfuse/telemetry). This contract promotes
that dropped structure into typed terminal events with uniform fields (§5).

---

## 2. Transport

| Operation | Method + path | Body | Response |
|---|---|---|---|
| Text turn | `POST /v1/turns:stream` | `application/json` (§4) | `text/event-stream` (§5) |
| Image turn | `POST /v1/turns:analyze-image` | `multipart/form-data` (envelope fields + `image` file) | `text/event-stream` (§5) |
| Non-streaming | `POST /v1/turns` | `application/json` | `application/json` — the terminal object (§5.3) with the full answer inlined |

`POST` (not the legacy `GET /chat/?...`) because `history`, coordinates, and future
context do not belong in a query string, and the image flow needs `multipart`.

### 2.1 Required request headers

Correlation and conversation identifiers travel in **headers**, not the JSON body —
they are cross-cutting transport concerns (logging, tracing, routing) that every
operation shares and that middleware reads without parsing the body.

| Header | Required | Meaning |
|---|:--:|---|
| `X-Session-Id` | ✅ | Conversation key. DSS reads `history` under it; the Experience API owns persistence (§1.2). |
| `X-Trace-Id` | ✅| Caller-supplied correlation id for tracing/telemetry (was bharat `qid`). DSS echoes it on responses and evidence; generates one if absent. |
| `Content-Type` | ✅ | `application/json` or `multipart/form-data`. |

`X-Session-Id` and `X-Trace-Id` are echoed back on the response (e.g.
`X-Trace-Id`, `Access-Control-Expose-Headers: X-Trace-Id`).

---

## 3. Identity & PII posture — **opaque reference only** (Posture A, §6.1)

The envelope carries **no raw personal data**. It does not carry `phone`,
`farmer_id`, `unique_id`, name, email, or a raw JWT claims dict. Identity is a single
`subject_ref` object minted by the Experience API:

```jsonc
"subject_ref": {
  "user_id":    "string",        // analytics/correlation id ONLY; "anonymous" if unknown.
                                  //   Not a credential, not resolvable to a person by the DSS.
  "ref":        "string|null",   // Provider-scoped opaque token, meaningful only to the
                                  //   intended Provider. DSS transports it to the Network
                                  //   Consumer Adapter; never persists, logs it.
  "issuer":     "string|null",   // who minted the ref (Experience API / tenant), for audit
  "expires_at": "string|null"    // RFC3339 validity bound; DSS treats expired refs as absent
}
```

- `user_id` lives **inside** `subject_ref` (there is no top-level `user_id`).
- `ref` is **required for any turn that needs a Provider capability acting on the
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

> **PII still leaks through free text.** `subject_ref` removes *structured* PII, but
> `query` and `history` are free text and can carry names, phone numbers, addresses. See
> §8.2 — this is an unresolved posture question, not a solved one.

---

## 4. Request envelope (`UserTurn`)

Body of `POST /v1/turns:stream` (with `X-Session-Id` / `X-Trace-Id` in headers, §2.1):

```jsonc
{
  // ── required core ─────────────────────────────────────────────
  "query":        "string",              // user utterance (see §8.2 re: PII in free text)
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
  "history":      [ /* TurnHistoryEntry[] — typed shape deferred, §8.1 */ ],

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
  `X-Session-Id` and `X-Trace-Id` are **required** in headers.
- `subject_ref.ref` is required for capability-backed turns; `subject_ref` may be
  anonymous otherwise.
- `history` may be `[]`. The DSS **reads** it and never writes to the Experience-owned
  store (§1.2). Persisting the new turn is the Experience API's job.
- `request_options` and all its members are optional.
- Anything not listed here is **rejected** — no raw identity dict, no framework handles
  (`background_tasks`), no LLM-tier flags (`pipeline_profile`, `use_translation_pipeline`),
  no `session_id`/`qid` in the body (they are headers now).

---

## 5. Response — hybrid: text stream + uniform terminal event

The answer body streams as plain text (legacy-compatible for existing renderers), then
**exactly one terminal event** closes the turn.

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
  "status":          "answered|rejected|no_match|needs_clarification|error",
  "cause":           "string|null",     // machine code; null only when status=answered
  "failure_details": { /* object */ } , // structured context for the outcome; null when answered
  "text":            "string"           // human-readable message (final answer when answered)
}
```

| Event | `status` | `cause` (examples) | `failure_details` (examples) | `text` |
|---|---|---|---|---|
| `turn.completed` | `answered` | `null` | `null` (see note below) | final answer text |
| `turn.rejected` | `rejected` | moderation category: `unsafe_illegal`, `invalid_non_agricultural`, `political_controversial`, `cultural_sensitive`, `role_obfuscation`, … | `{ "category": "...", "policy": "..." }` | localized decline text |
| `turn.no_match` | `no_match` | `no_capability` \| `insufficient_result` | `{ "attempted_capabilities": [...] }` | user-facing no-match message |
| `turn.clarification` | `needs_clarification` | `ambiguous` | `{ "missing": ["district", "crop"] }` | the clarifying question |
| `turn.error` | `error` | `provider_unavailable` \| `timeout` \| `internal` | `{ "retryable": true }` | user-facing error message |

> **Answered metadata.** Provenance, confidence, limitations, and next-steps ride in
> `failure_details` for a successful turn as well (the field name is a legacy of the
> failure-first framing; treat it as "structured details"). For `answered`:
> `failure_details = { "provenance": [...], "confidence": "high|low", "limitations": [...], "next_steps": [...] }`.
> If you would rather rename this field to a neutral `details`, that is a small change —
> flagged in §8.4.

`cause` for `rejected` is the moderation model's category verbatim (`valid_agricultural`,
`invalid_non_agricultural`, `invalid_external_reference`, `invalid_compound_mixed`,
`invalid_language`, `unsafe_illegal`, `political_controversial`, `cultural_sensitive`,
`role_obfuscation`). Legacy muxed the decline as a text chunk; here it is a typed
terminal event, and a client that renders only `content.delta` shows nothing spurious.

### 5.3 Non-streaming (`POST /v1/turns`)

Returns the single terminal object (§5.2 schema) with the full answer in `text`:

```jsonc
{ "status": "answered", "cause": null,
  "failure_details": { "provenance": [...], "confidence": "high", "limitations": [], "next_steps": [] },
  "text": "<full answer text>" }
```

---

## 6. What stays DSS-internal (never on the wire)

History persistence (Redis `{session_id}_SVA`), suggestions generation, telemetry &
Langfuse tracing, LLM tier / route / fallback selection, translation batching &
sentence segmentation, `<think>`-block stripping, NPSS post-processing, disconnect-safe
finalization. Callers see only the headers + envelope in (§2.1, §4) and the event stream
out (§5).

Operational evidence (§6.3) — interaction id, stage, selected capability, policy /
moderation / routing / review outcome, latency, terminal outcome — is emitted to the
evidence sink **without request/response content**, keyed by `X-Trace-Id`.

---

## 7. Per-repo migration notes

- **amul** → drop `use_translation_pipeline` / `pipeline_profile` (tenant config now);
  `phone`-driven farmer context resolves behind `subject_ref.ref`; `response_max_chars`
  moves to `request_options`; `session_id` → `X-Session-Id` header.
- **bharat** → `qid` → `X-Trace-Id` header; `session_id` → `X-Session-Id` header;
  `is_image_analysis` → `modality:"image"` on the image endpoint; `latitude`/`longitude`
  → `request_options.coordinates`; rich JWT stays at Experience layer.
- **mh** → add explicit `channel`; `session_id` → `X-Session-Id` header;
  `farmer_id`/`unique_id` no longer on the envelope — resolved via `subject_ref.ref`.

---

## 8. Open points

### 8.1 Carried from architecture §8.3 (DSS-scoped, deferred to v1 design)

- **Minimum DSS contracts** — request, response, tool, context, evidence, error —
  including the **plan schema** (§5.5), since the plan is a first-class artifact policy
  evaluates and evidence records.
- **Request envelope `history` typing** — concrete `TurnHistoryEntry` shape (roles,
  tool-call trace inclusion, redaction posture). This contract leaves `history` untyped.
- **`UserDetails` / `subject_ref` extensibility** — whether tenant profile fields
  (farmer ID, region, land size) attach through the opaque `ref` resolution only, or
  whether a non-personal `user_context` projected by Context Providers is also needed.
- **Translation boundary and personal-data classification** — which operating mode
  (translate-then-reason vs reason-in-native, §5.1) a tenant runs, and what that makes
  the DSS a processor of.
- **DSS-internal caches vs user context** — intent/selection caches (§5.2, §5.4) must
  stay a distinct store from Experience-owned history/profile; boundary enforcement open.
- **Evaluation thresholds, confidence categories, human-escalation requirements** —
  affects the `confidence` value space and any escalation surfaced in a terminal event.

### 8.2 ⚠️ PII passing through `query` and `history` — UNRESOLVED

`subject_ref` (§3) removes *structured* identity from the envelope, but **free text is
not covered**:

- **`query`** can contain a spoken/typed name, phone number, address, Aadhaar-like
  number, or land-record id. Does the DSS ever see raw-PII `query` text? If so, is
  pre-DSS scrubbing an **Experience-layer** responsibility or a **DSS** one? (§8.3 open Q2)
- **`history`** replays prior user turns — the same free-text PII risk, multiplied, and
  it is also **untrusted input** to enrichment (§3.0): prior turns are data, never
  instructions.
- Sink-layer redaction (§6.2) protects *persisted* artifacts (logs, traces), but does
  **not** stop raw PII from entering prompts, tool registries, or the model context in
  the first place — that is the Posture-A vs Posture-B tension in §8.3.
- If PII may enter in-flight, do we need **per-Provider forwarding allowlists** (which
  fields flow to which capability) in addition to sink redaction? (§8.3 open Q3)
- **Personalisation** ("Hi Ramesh…") when the DSS cannot see the name — templated
  response with post-DSS substitution by the participating deployment, or opaque
  user-segment tokens? (§8.3 open Q4)

**Working assumption of this contract:** the Experience API scrubs PII from `query` and
`history` before the DSS sees them. This must be confirmed before the v1 envelope is
locked; if it is not viable, the envelope and forwarding rules change materially.

### 8.3 Contract-specific open points

- **Exact provenance / confidence schema** inside the `answered` `failure_details`.
- **`cause` code registry** — the enumerated machine codes per `status`, versioned so
  callers can switch on them safely.
- **Image turn envelope** — how `subject_ref` and `request_options` are encoded as
  `multipart/form-data` fields alongside the `image` part.

### 8.4 Naming nit to settle

`failure_details` (§5.2) also carries success metadata for `answered` turns, which reads
oddly. Options: keep `failure_details` for uniformity as requested; or rename to a
neutral `details` while keeping the same four-field uniform schema. Decide before lock.

---

## 9. Worked examples (curl)

Illustrative only — host, ids, and text are placeholders. SSE responses show the raw
`event:`/`data:` frames the server writes; `--no-buffer` (`-N`) keeps them unbuffered.

### 9.1 Streaming — success (`turn.completed`)

**Request**

```bash
curl -N -X POST https://dss.example.internal/v1/turns:stream \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: sess_8f3a1c" \
  -H "X-Trace-Id: trace_2024-06-11_abc123" \
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
`X-Trace-Id: trace_2024-06-11_abc123`

```
event: content.delta
data: {"text": "इस सप्ताह आपके जिले में गेहूं का मंडी भाव "}

event: content.delta
data: {"text": "लगभग ₹2,275 प्रति क्विंटल है।"}

event: turn.completed
data: {"status":"answered","cause":null,"failure_details":{"provenance":[{"capability":"mandi_price_lookup","source":"Agmarknet","url":"https://agmarknet.gov.in/..."}],"confidence":"high","limitations":[],"next_steps":[]},"text":"इस सप्ताह आपके जिले में गेहूं का मंडी भाव लगभग ₹2,275 प्रति क्विंटल है।"}
```

### 9.2 Streaming — failure (`turn.rejected`, moderation)

A non-`answered` terminal can arrive with **zero** preceding `content.delta`.

**Request**

```bash
curl -N -X POST https://dss.example.internal/v1/turns:stream \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: sess_8f3a1c" \
  -H "X-Trace-Id: trace_2024-06-11_def456" \
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

**Response** — `200 OK`, `text/event-stream` (the turn is handled, the *content* is refused)

```
event: turn.rejected
data: {"status":"rejected","cause":"unsafe_illegal","failure_details":{"category":"unsafe_illegal","policy":"moderation.safety"},"text":"I can only answer agriculture and livestock related questions."}
```

Other failure terminals use the identical four-field schema, e.g. a provider outage:

```
event: turn.error
data: {"status":"error","cause":"provider_unavailable","failure_details":{"retryable":true},"text":"I am unable to process your request right now. Please try again later."}
```

### 9.3 Non-streaming — success (`POST /v1/turns`)

**Request**

```bash
curl -X POST https://dss.example.internal/v1/turns \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: sess_8f3a1c" \
  -H "X-Trace-Id: trace_2024-06-11_ghi789" \
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
  "failure_details": {
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
  -H "X-Trace-Id: trace_2024-06-11_jkl012" \
  -d '{
    "query": "Book me a flight to Paris",
    "source_lang": "en", "target_lang": "en", "channel": "web",
    "subject_ref": { "user_id": "anonymous", "ref": null, "issuer": "experience-api", "expires_at": null },
    "history": [],
    "request_options": { "modality": "text", "stream": false }
  }'
```

**Response** — `200 OK`, `application/json` (the turn ran; no capability fit — §7 no-match, not a 4xx)

```json
{
  "status": "no_match",
  "cause": "no_capability",
  "failure_details": { "attempted_capabilities": [] },
  "text": "I couldn't find a way to help with that. I can assist with agriculture and livestock questions."
}
```

> **HTTP status convention.** A turn the DSS *processed* returns `200` even when the
> outcome is `rejected` / `no_match` / `error` — the outcome lives in the terminal
> event's `status`, not the HTTP code. Reserve non-2xx for transport/validation faults
> (`400` malformed envelope, `401` bad/absent auth at the Experience edge, `422` schema
> violation). Settle this convention before lock alongside the `cause` registry (§8.3).
