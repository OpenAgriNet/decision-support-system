# DSS API Contract

**Status:** Draft (v1). **Who calls this:** the channel services of one deployment — chat,
voice, and any other.

One turn in. A stream of sentences out. Then one final event.

For boundary and posture, see `docs/DSS_ARCHITECTURE.md`. Decisions are in §9, open items
in §8.

---

## 1. What the DSS owns

| Owns | Does not own |
|---|---|
| Reading the question and answering it | Authentication and authorization |
| Writing the answer for the channel | Sessions and history storage |
| Citing where each fact came from | Per-user rate limits |

The DSS stores nothing that outlives a turn, except its own evidence (§6).

---

## 2. Transport

REST over HTTP. Server-Sent Events for streaming.

| Operation | Method and path | Request | Response |
|---|---|---|---|
| Streaming turn | `POST /v1/turns:stream` | `application/json` | `text/event-stream` |
| Image turn | `POST /v1/turns:analyze-image` | `multipart/form-data` | `text/event-stream` |
| Non-streaming turn | `POST /v1/turns` | `application/json` | `application/json` |

`POST` because `history` does not belong in a query string, and images need `multipart`.

### 2.1 Headers

| Header | Required | What it is |
|---|:--:|---|
| `X-Session-Id` | Yes | The conversation key. The caller owns storage. |
| `X-Trace-Id` | No | Per-turn id. Opaque, non-personal. The DSS makes one if absent. |
| `Content-Type` | Yes | `application/json` or `multipart/form-data` |

Both are echoed back. `trace_id` is **also in the final event body** — a header is lost the
moment the caller stores the answer, and the audit trail needs the join key to survive.

No CORS headers. Only this deployment's own services reach this port.

### 2.2 Versioning

Version is in the path (`/v1/...`). Adding a field does not bump it. Changing or removing
one introduces `/v2`.

### 2.3 Trust boundary

**This API is internal.** The DSS runs in the deployment's private subnet. Only that
deployment's own channel services call it. No browser, no mobile app, no third-party SDK.

- No authentication. Network policy keeps others out. The DSS never returns `401` or `403`.
- **No authorization.** A valid assertion means the turn runs. What a farmer may do is
  decided upstream. If a Provider refuses a call, that comes back as a failure and the
  answer says so.
- Rate limits belong to the caller. The DSS may cap total concurrency.

**Why this format and not OpenAI or Open Responses.** Those formats exist so a client team
can reuse an SDK. No client team is on this boundary. They also stream model text, which
has nowhere to put the two things this contract is built on: a source id per sentence, and
an answer already written for its channel.

If a client ever wants an OpenAI-shaped API, that is a channel adapter in the chat service.
The OAN architecture already allows it. The DSS does not change.

### 2.4 Retries — not safe in v1

A turn sent twice runs twice, including any Provider call it makes. `X-Trace-Id` is for
correlation, not replay protection.

Closing this needs a caller-supplied id that survives a retry, plus a store of past
outcomes. Neither exists. Until then, treat a failed turn as possibly already done.

### 2.5 Dropped connections

The stream cannot be resumed. If the connection drops, the DSS finishes the turn and saves
the answer to the session. The caller re-issues with the same `X-Session-Id` and finds it
in history.

### 2.6 Status codes

A turn the DSS processed returns `200`, whatever the outcome. Refusals and errors are in
the final event, not the HTTP code.

| Code | Meaning |
|---|---|
| `400` | Malformed request |
| `422` | Well-formed but invalid |
| `429` | Concurrency cap reached. Carries `Retry-After`. |
| `503` | DSS unavailable |

---

## 3. Identity

Identity is one opaque object. No phone, no name, no email, no JWT claims.

```jsonc
"subject_ref": {
  "user_id":    "string",        // canonical id. "anonymous" if unknown
  "ref":        "string|null",   // token for Provider calls. The DSS passes it on,
                                 //   never opens, resolves, stores, or logs it
  "issuer":     "string|null",   // who made the ref
  "expires_at": "string|null"    // RFC3339. An expired ref counts as absent
}
```

- `user_id` is a canonical id. The caller maps a phone or email to it and keeps that table.
  The DSS never sees the real identifier and cannot reverse the id.
- `ref` is needed for any turn that calls a Provider on the farmer's behalf. Otherwise the
  turn can be anonymous.
- Personal data a Provider needs travels the caller→Provider path, not the prompt.

**Free text.** `query` and `history` are scrubbed by the caller. The DSS redacts again on
write to its sinks (§6).

---

## 4. Request

```jsonc
{
  "query":       "string",              // what the farmer asked, already scrubbed
  "source_lang": "string",              // BCP 47
  "target_lang": "string",              // BCP 47
  "channel":     "web|whatsapp|voice|sms",

  "subject_ref": { "user_id": "string", "ref": "string|null",
                   "issuer": "string|null", "expires_at": "string|null" },

  "location": {                         // optional
    "region":   "string|null",          // ISO 3166-2, e.g. "IN-GJ", "IN-CH"
    "area":     "string|null",          // local name, e.g. "Anand"
    "geometry": { "type": "Point", "coordinates": [72.93, 22.56] }   // [lon, lat]
  },

  "history": [ /* TurnHistoryEntry[] — { role, content, … }. Fields open (§8) */ ],

  "request_options": { "modality": "text|image", "response_max_chars": 0 }
}
```

Rules:

- `query`, `source_lang`, `target_lang`, `channel` are required. So is `X-Session-Id`.
- Languages are BCP 47, checked against a list the DSS publishes. Adding a language is not
  a contract change.
- `region` is **ISO 3166-2**. One field covers states, union territories, and places outside
  India. Chandigarh is `IN-CH`, not a district that happens to be a state.
- `area` is free text. There is no governed list of Indian districts to validate against.
- `geometry` is Beckn **`GeoJSONGeometry` v2.0** (RFC 7946, WGS-84). Coordinates are
  **`[longitude, latitude]`** — GeoJSON order, the reverse of what the current deployments
  send. `Polygon` works too, so a caller can send a served area instead of a point.
- **`geometry` is used for planning and never written to the sinks (§6).**
- `history` may be empty. The DSS reads it and never writes to the caller's store.
- **The endpoint decides streaming, not a flag.** `/v1/turns:stream` streams. `/v1/turns`
  does not.
- `response_max_chars` is the caller's cap. The DSS writes to fit it.
- Unknown fields are rejected.

---

## 5. Response

Sentences stream, then one final event. Only an answered turn streams — the other outcomes
send the final event alone.

### 5.1 Claim events

A claim is **one whole sentence**, finished when sent. Not a character delta.

```
event: claim
data: {"text": "Potato grows best in well-drained sandy loam soil.", "source_id": "1"}
```

`source_id` is `null` for a linking sentence that cites nothing.

**The DSS writes for the channel.** Voice gets words made for speaking — short sentences,
no bullets, amounts spelled out. Chat gets words made for reading. This happens in the DSS
because writing differently needs a language model, and no language model runs outside it.

The caller groups sentences into what it can send and renders or strips the citation
markers. It does not rewrite.

### 5.2 Final event

```jsonc
{
  "status":   "answered|rejected|no_match|needs_clarification|error",
  "cause":    "string|null",     // null when answered
  "text":     "string",          // the whole answer
  "sources":  [ { "id": "1", "name": "Agmarknet", "kind": "provider", "url": "string|null" } ],
  "trace_id": "string"
}
```

**Five fields, and something reads each one.** `status` and `cause` let the caller tell a
refusal from a crash — today's deployments cannot, because both look the same on the wire.
`sources` carries provenance. `trace_id` joins the answer to its audit record.

**Everything the farmer reads is in `text`.** Refusals, caveats, and follow-up questions are
already sentences there, in the right language for the channel. A second structured copy
would be a second source of truth, and the untested one. Refusals and skipped inputs still
go to the diagnostic sink (§6), which is where "how often did we refuse, and why" is
answered.

**`status` on a partial answer.** A farmer asking three things may get two answers and one
refusal:

| Answered anything | Follow-up needed | `status` |
|:--:|:--:|---|
| yes | no | `answered` |
| yes | yes | `answered` |
| no | yes | `needs_clarification` |
| no | no | `no_match` |

**`text` is authoritative.** On a streaming turn it repeats the sentences already sent. A
caller rendering live must not append it too. It is there for the non-streaming case and for
storage.

**No capability names on the wire.** `sources` carries what the farmer sees. Which
capability produced it goes to telemetry.

`cause` is a closed, versioned list. An unknown `cause` should be treated as the generic
case of its `status`, so adding one is not a breaking change.

| Group | Codes |
|---|---|
| harm | `unsafe_illegal`, `role_obfuscation`, `political_controversial`, `external_reference`, `adopter_policy` |
| scope | `domain_unmapped`, `intent_low_confidence`, `unsupported_action_type` |
| infrastructure | `unavailable`, `provider_unavailable`, `timeout`, `internal` |

### 5.3 Faults

A finished turn always ends with a final event. If the DSS fails mid-stream it sends one
with `status: "error"` before closing. If the connection drops, none arrives — recover per
§2.5.

### 5.4 Non-streaming

`POST /v1/turns` returns the same object, no claim events, full answer in `text`.

---

## 6. Evidence

Not part of the wire contract: history storage, telemetry, model selection, translation,
image post-processing.

Two tiers, both keyed by `trace_id`:

| Tier | Holds | Kept | Who reads |
|---|---|---|---|
| Metrics | stage, capability used, moderation and routing outcomes, latency, outcome. No user content. | long | broad |
| Diagnostic | the above plus query, answer, sources, refusals, skipped inputs | short | restricted |

The diagnostic tier holds farmer content on purpose. Metadata tells you *that* something
changed, not *what went wrong*. It is also where refusals are counted — the three current
deployments each compute a moderation category and throw it away, so today that question can
only be answered by reading prose in four languages.

**Redaction happens on write to a sink**, not at the request boundary.

**Location is cut at the sink.** `region` and `area` are written. `geometry` is not. A
stable `user_id` next to a precise point, over many turns, is a home location.

---

## 7. Migration

The DSS replaces `stream_chat_messages` in the amul, bharat, and mh deployments.

| Legacy parameter | Where it goes |
|---|---|
| `query`, `source_lang`, `target_lang`, `channel` | Body, same names |
| `session_id` | Header `X-Session-Id` |
| `qid` | Header `X-Trace-Id` |
| `user_id` | Into `subject_ref` |
| `history` | Body `history`, read-only |
| `latitude` / `longitude` | `location.geometry` — **reverse the pair to `[lon, lat]`** |
| `is_image_analysis` | `request_options.modality`. Today bharat derives it by sniffing the query, not from a parameter. |
| `user_info` / `current_user` | Dropped — use `subject_ref` |
| `background_tasks` | Dropped — the DSS owns its own async work |
| `use_translation_pipeline`, `pipeline_profile` | Dropped — DSS config, not caller flags |

Per deployment:

- **amul** — drop the pipeline flags; move phone-driven farmer context behind
  `subject_ref.ref`; `response_max_chars` moves to `request_options`.
- **bharat** — JWT claims stay at the caller; reverse the coordinate pair.
- **mh** — add an explicit `channel`; resolve `farmer_id`/`unique_id` via `subject_ref.ref`.

Two things to plan for, common to all three:

- Today a blocked turn and a crashed turn look the same to the client. `status` and `cause`
  tell them apart.
- All three send bare text to their frontends under a `text/event-stream` content type.
  They can keep doing that and parse SSE only on the DSS side. Passing frames through to the
  frontend is a separate, breaking change.

---

## 8. Open

- **`ref` resolution.** What it resolves to and who validates it. Pending the Network
  Consumer Adapter design.
- **`TurnHistoryEntry` fields.** Direction is fixed, fields are not.
- **`cause` list.** Shape is fixed; the full list per status is to be published.
- **Retries.** A caller id that survives a retry, plus a store of past outcomes (§2.4).
- **Telemetry retention and access.** Sharper than it looks — `user_id` is stable, so
  diagnostic rows link across sessions. Open: how long, who reads, are reads audited.
- **Image turns.** Nothing designed beyond the endpoint and the `modality` flag. Whether the
  DSS handles images at all is unsettled — including how the envelope maps to multipart.
- **Free-text PII.** Scrub quality, per-Provider allowlists, personalization without names.
- **Translation mode.** Translate-then-reason or reason-in-native, per tenant.
- **Provider-scoped refs.** The OAN architecture says each Provider gets a different scoped
  reference. This contract carries one. Deferred until a second Provider actually receives
  one and the two are run by different parties.

---

## 9. Decisions

| Decision | Choice |
|---|---|
| API format | DSS-native. Not OpenAI, Claude, or Open Responses — this API is internal (§2.3) |
| Transport | REST + SSE (below) |
| Authentication | None. Network perimeter (§2.3) |
| Authorization | None in the DSS. Valid assertion, turn runs (§2.3) |
| Streaming unit | Claims — one whole sentence, with `source_id` (§5.1) |
| Who writes for the channel | The DSS. The caller groups and renders (§5.1) |
| Final event | Five fields: `status`, `cause`, `text`, `sources`, `trace_id` (§5.2) |
| What the farmer reads | All of it in `text` — refusals and follow-ups included (§5.2) |
| Capability names on the wire | None. `sources` carries farmer-visible names (§5.2) |
| Evidence | Two tiers, keyed by `trace_id` (§6) |
| Location in the sinks | `region` and `area` only. Never `geometry` (§6) |
| Location shape | ISO 3166-2 + free-text area + Beckn `GeoJSONGeometry` v2.0 (§4) |
| Identity | One opaque `subject_ref`. Canonical `user_id`, never reversible by the DSS (§3) |
| Redaction | On write to a sink, not at the request boundary (§6) |
| `trace_id` | Header **and** final event body, so it survives storage (§2.1) |
| HTTP status | `200` for any processed turn (§2.6) |
| Versioning | URI path. Additive changes do not bump (§2.2) |
| Who decides streaming | The endpoint, not a flag (§4) |
| Retries | Deferred. Replays re-execute (§2.4) |
| Reconnection | Not resumable. The DSS finishes server-side (§2.5) |

### Why REST + SSE

The DSS is a separate container, so a network boundary exists anyway. A turn is inherently
streaming. The teams already run SSE over HTTP. No new tooling.

gRPC was rejected — protobuf toolchains for little gain inside one subnet. An in-process
call was rejected — it contradicts the separate-container model. SSE is one-way after the
`POST`, which is enough for a turn. Voice barge-in would reopen this.

### One id per turn

`trace_id` is the per-turn id, supplied as `X-Trace-Id` (was `qid`), echoed back, and used
as the evidence key. Opaque and non-personal. The DSS makes one when the caller omits it.

`session_id` spans many turns. These are the only two ids. `interaction_id`,
`correlation_id`, `qid`, and `request_id` are not used as synonyms.

---

## 10. Examples

Every processed turn returns `200`.

### Answered, streaming

```bash
curl -N -X POST https://dss.internal/v1/turns:stream \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: sess_8f3a1c" \
  -H "X-Trace-Id: trc_9f2b7c1a" \
  -d '{
    "query": "What is the mandi price of wheat in my district this week?",
    "source_lang": "hi", "target_lang": "hi", "channel": "web",
    "subject_ref": { "user_id": "usr_9921", "ref": "prov_opaque_7b2f...e0",
                     "issuer": "experience", "expires_at": "2026-08-24T18:30:00Z" },
    "location": { "region": "IN-GJ", "area": "Anand",
                  "geometry": { "type": "Point", "coordinates": [72.93, 22.56] } },
    "history": [],
    "request_options": { "modality": "text", "response_max_chars": 1200 }
  }'
```

```
event: claim
data: {"text":"इस सप्ताह आनंद मंडी में गेहूं का भाव ₹2,275 प्रति क्विंटल है।","source_id":"1"}

event: turn.completed
data: {"status":"answered","cause":null,"text":"इस सप्ताह आनंद मंडी में गेहूं का भाव ₹2,275 प्रति क्विंटल है।","sources":[{"id":"1","name":"Agmarknet","kind":"provider","url":"https://agmarknet.gov.in/..."}],"trace_id":"trc_9f2b7c1a"}
```

### Rejected

No claim events. `X-Trace-Id` was omitted, so the DSS made one.

```
event: turn.rejected
data: {"status":"rejected","cause":"unsafe_illegal","text":"I can only answer agriculture and livestock related questions.","sources":[],"trace_id":"trc_minted_4a1f"}
```

A Provider outage uses the same shape:

```
event: turn.error
data: {"status":"error","cause":"provider_unavailable","text":"I am unable to process your request right now. Please try again later.","sources":[],"trace_id":"trc_minted_4a1f"}
```

### No match, non-streaming

```json
{
  "status": "no_match",
  "cause": "domain_unmapped",
  "text": "I couldn't find a way to help with that. I can assist with agriculture and livestock questions.",
  "sources": [],
  "trace_id": "trc_a17c3e90"
}
```

### The partial case

*"Which soil is best for potato? What is the price of potato and gold?"* — two asks served,
gold refused, and the price lookup short of a location.

```json
{
  "status": "answered",
  "cause": null,
  "text": "Potato grows best in well-drained sandy loam soil. Keep soil pH between 5.2 and 6.4. I cannot help with gold prices. Which district should I check potato prices for?",
  "sources": [ { "id": "1", "name": "ICAR Potato Guide", "kind": "document", "url": "https://icar.example/..." } ],
  "trace_id": "trc_5b8d2f11"
}
```

`status` is `answered` because something was answered. The refusal and the follow-up
question are sentences in `text`. The caller renders them as they are.
