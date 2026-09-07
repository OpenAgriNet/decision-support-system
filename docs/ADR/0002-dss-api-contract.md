# ADR-0002: DSS-Native REST + SSE API Contract for the Experience Layer

- **Status:** PROPOSED (out for review with the experience-layer team)
- **Date:** 2026-08-31
- **Deciders:** OAN (OpenAgriNet) DPG architecture group
- **Consulted:** Experience-layer engineering leads (chat and voice channel services); adopter engineering leads across current deployments; Product Owner
- **Informed:** Adopter engineering teams; OAN DPG steward

---

## 1. Context and Problem Statement

The DSS is a reasoning runtime that takes **one turn in** and returns **a stream of sentences out, then one final event.** It is an Experience-Layer module of the OAN DPG: its only callers are the channel services of a single deployment — chat, voice, and any other the deployment runs. It replaces the existing `stream_chat_messages` flow in current deployments.

A contract has to be fixed for this boundary before the channel services can integrate against it. The question is not only *what fields cross the wire*, but **what shape the API takes**: a DSS-native REST + SSE contract, or a compatibility surface that mirrors an existing LLM API — OpenAI Chat Completions, Claude Messages, or the OpenAI **Responses** API.

The pull toward a compatible surface is real: those formats ship SDKs, and reusing one would let a client team integrate with tooling they already know. This ADR records why that pull does not apply at this boundary, and fixes the native contract instead.

What the contract must carry, and what no LLM-shaped format carries natively:

- **A source id per sentence.** Every claim the DSS streams cites where the fact came from.
- **An answer already written for its channel.** Voice gets spoken-form sentences; chat gets read-form. The DSS writes for the channel because that rewrite needs a language model, and no language model runs outside the DSS.
- **Five distinct turn outcomes** — `answered`, `rejected`, `no_match`, `needs_clarification`, `error` — each of which a caller must be able to tell apart on the wire.
- **A closed, versioned `cause` list** the caller can branch on.
- **Identity as one opaque object** (`subject_ref`) whose `ref` is a secret that is never inspected, resolved, persisted, or logged.
- **Location** as ISO 3166-2 region + free-text area + Beckn `GeoJSONGeometry` v2.0.

**The question.** What API contract does the DSS expose to the experience layer — a DSS-native REST + SSE format, or an OpenAI/Claude/Open-Responses-compatible surface?

---

## 2. Decision Drivers

Ordered by weight:

1. **Who actually calls this boundary.** The DSS runs in the deployment's private subnet and is called only by that deployment's own channel services. There is no browser, no mobile app, no third-party SDK, and no external UI team on this boundary. A compatibility format solves a problem — *let an unknown client reuse an SDK* — that does not exist here.
2. **The two things the contract is built on must be first-class:** a source id per sentence, and an answer already written for its channel. A format with nowhere to put these is the wrong substrate.
3. **All five outcomes must be legible on the wire.** A caller must distinguish a refusal from a crash, and a `no_match`/`needs_clarification` from a real answer — today's deployments cannot, and that is a defect the contract exists to fix.
4. **Custom `cause` codes the caller can branch on.** The caller changes course based on *why* a turn ended; a fixed vendor error enum cannot express our reasons.
5. **Security posture of an internal boundary.** Identity and location must not leak back to the caller or into logs; image input must not open an SSRF hole.
6. **Migration cost for the three existing deployments.** They already run SSE over HTTP; the contract should meet them where they are.
7. **Reversibility.** If an external, SDK-shaped client ever appears, adding compatibility must not require changing this contract.

---

## 3. Considered Options

- **Option A — DSS-native REST + SSE.** A purpose-built contract: `POST` a turn, receive `text/event-stream` claim events, then one final event.
- **Option B — OpenAI Responses-compatible surface.** Expose `POST /v1/responses` with the OpenAI Responses schema, mapping DSS concepts onto `output`, `annotations`, `refusal`, and `metadata`.
- **Option C — OpenAI Chat Completions / Claude Messages-compatible surface.** An older chat-shaped format. Strictly weaker than Option B for this problem (no native per-span citations, no typed refusal), so it is folded into Option B's rejection below rather than analysed separately.

---

## 4. Decision Outcome

**Chosen option: Option A — DSS-native REST + SSE.**

The DSS exposes a native contract. No LLM-vendor-compatible surface is adopted at this boundary. The essential shape follows; the full contract with all rules and examples lives in `docs/api-contracts/api-contract.md`, and the evaluated-but-rejected Responses mapping in `docs/api-contracts/open-responses-api.md`.

### 4.1 Transport

REST over HTTP. Server-Sent Events for streaming.

| Operation | Method and path | Request | Response |
|---|---|---|---|
| Streaming turn | `POST /v1/turns:stream` | `application/json` | `text/event-stream` |
| Image turn | `POST /v1/turns:analyze-image` | `multipart/form-data` | `text/event-stream` |
| Non-streaming turn | `POST /v1/turns` | `application/json` | `application/json` |

`POST` because `history` does not belong in a query string, and images need `multipart`. **The endpoint decides streaming, not a flag.**

**Headers**

| Header | Required | What it is |
|---|:--:|---|
| `X-Session-Id` | Yes | The conversation key. The caller owns storage. |
| `X-Trace-Id` | No | Per-turn id. Opaque, non-personal. The DSS makes one if absent. |
| `Content-Type` | Yes | `application/json` or `multipart/form-data` |

Both are echoed back. `trace_id` is **also in the final event body** — a header is lost the moment the caller stores the answer, and the audit trail needs the join key to survive. No CORS headers; only this deployment's own services reach this port.

**Versioning.** Version is in the path (`/v1/...`). Adding a field does not bump it. Changing or removing one introduces `/v2`.

**Status codes.** A turn the DSS processes returns `200`, whatever the outcome. Refusals and errors are in the final event, not the HTTP code.

| Code | Meaning |
|---|---|
| `400` | Malformed request |
| `422` | Well-formed but invalid |
| `429` | Concurrency cap reached. Carries `Retry-After`. |
| `503` | DSS unavailable |

### 4.2 Request

```jsonc
{
  "query":       "string",              // what the farmer asked, already scrubbed
  "source_lang": "string",              // BCP 47
  "target_lang": "string",              // BCP 47
  "channel":     "web|whatsapp|voice|sms",

  "subject_ref": {                      // one opaque object. No phone, name, email, or JWT claims
    "user_id":    "string",             // canonical id. "anonymous" if unknown
    "ref":        "string|null",        // token for Provider calls. Passed on, never opened, resolved, stored, or logged
    "issuer":     "string|null",        // who made the ref
    "expires_at": "string|null"         // RFC3339. An expired ref counts as absent
  },

  "location": {                         // optional
    "region":   "string|null",          // ISO 3166-2, e.g. "IN-GJ", "IN-CH"
    "area":     "string|null",          // local name, e.g. "Anand"
    "geometry": { "type": "Point", "coordinates": [72.93, 22.56] }   // [lon, lat]
  },

  "history": [ /* TurnHistoryEntry[] — { role, content, … } */ ],

  "request_options": { "modality": "text|image", "response_max_chars": 0 }
}
```

Key rules:

- `query`, `source_lang`, `target_lang`, `channel` are required. So is `X-Session-Id`.
- Languages are BCP 47, checked against a list the DSS publishes. Adding a language is not a contract change.
- `user_id` is a canonical id the caller maps a phone or email onto; the DSS never sees the real identifier and cannot reverse the id. `ref` is needed only for a turn that calls a Provider on the farmer's behalf.
- `region` is ISO 3166-2 (Chandigarh is `IN-CH`). `area` is free text. `geometry` is Beckn `GeoJSONGeometry` v2.0 (RFC 7946, WGS-84) — coordinates are `[longitude, latitude]`, the reverse of what the current deployments send. `geometry` is used for planning and never written to the sinks.
- Unknown fields are rejected.

### 4.3 Response

Sentences stream, then one final event. Only an **answered** turn streams claims — the other outcomes send the final event alone.

**Claim events** — one whole sentence, finished when sent, not a character delta:

```
event: claim
data: {"text": "Potato grows best in well-drained sandy loam soil.", "source_id": "1"}
```

`source_id` is `null` for a linking sentence that cites nothing.

**Final event** — five fields, and something reads each one:

```jsonc
{
  "status":   "answered|rejected|no_match|needs_clarification|error",
  "cause":    "string|null",     // null when answered; a closed, versioned list otherwise
  "text":     "string",          // the whole answer — everything the farmer reads
  "sources":  [ { "id": "1", "name": "Agmarknet", "kind": "provider", "url": "string|null" } ],
  "trace_id": "string"
}
```

`status` and `cause` let the caller tell a refusal from a crash. `sources` carries provenance. `trace_id` joins the answer to its audit record. **Everything the farmer reads is in `text`** — refusals, caveats, and follow-up questions are already sentences there, in the right language for the channel. `cause` is a closed, versioned list; an unknown `cause` is treated as the generic case of its `status`, so adding one is not a breaking change.

| Group | Codes |
|---|---|
| harm | `unsafe_illegal`, `role_obfuscation`, `political_controversial`, `external_reference`, `adopter_policy` |
| scope | `domain_unmapped`, `intent_low_confidence`, `unsupported_action_type` |
| infrastructure | `unavailable`, `provider_unavailable`, `timeout`, `internal` |

The non-streaming endpoint (`POST /v1/turns`) returns the same object with no claim events and the full answer in `text`. On a fault mid-stream the DSS sends a final event with `status: "error"` before closing; if the connection drops, none arrives and the caller recovers by re-issuing with the same `X-Session-Id`.

### 4.4 Positive Consequences

- **The two load-bearing concepts are first-class.** A sentence and its `source_id` travel in one frame; the channel-shaped answer is just the text the DSS wrote.
- **All five outcomes are legible on the wire** — `status` + `cause` — closing the "was it a refusal or a crash?" gap the three deployments have today.
- **Custom `cause` codes let the caller branch on why a turn ended.**
- **The internal posture is honest:** no auth, no CORS, `ref` as a secret, no image URL fetch — because only in-subnet services call this.
- **Migration is incremental.** The three deployments already run SSE over HTTP; they can parse SSE on the DSS side and keep passing bare text to their frontends. The parameter-by-parameter mapping is in `api-contract.md` §7.

### 4.5 Negative Consequences

- **The contract is bespoke.** A future external client cannot point a stock OpenAI/Claude SDK at it. This is mitigated by §4.6: such a client is served by an adapter *in front of* the DSS, not by changing this contract.
- **Retries are not safe in v1.** A turn sent twice runs twice. Closing this needs a caller-supplied idempotency id and a store of past outcomes; neither exists yet.
- **The stream is not resumable.** A dropped connection is recovered from session history, not replayed.

### 4.6 If a compatible surface is ever needed, it goes in front — not inside

If an SDK-shaped client ever appears, the answer is a **channel adapter in the chat service**, not a change to this contract:

```
SDK client ──> Responses/OpenAI adapter ──> DSS (native API) ──> answer
```

The OAN architecture already allows this. The DSS API stays clean, and a second format later is a second adapter rather than a second contract.

---

## 5. Rejection Rationale — why not OpenAI Responses (or any LLM-vendor format)

The Responses API was evaluated in full (`docs/api-contracts/open-responses-api.md`, checked against the OpenAI OpenAPI spec, August 2026). It is the strongest of the compatible options — it has native per-span citations and a typed refusal — and it is still not adopted. The reasons, in order of weight:

### 5.1 No caller exists that a compatible format would serve

The single decisive reason. These formats exist so a client team can reuse an SDK. **No client team is on this boundary** — the DSS is called only by our own chat and voice services inside a private subnet. A compatible format solves a problem that does not exist here, and every cost below is paid for that non-existent benefit.

### 5.2 The exchange is retrofitted onto `metadata`, not a natural fit

Everything the format has no field for — `channel`, `source_lang`/`target_lang`, `region`/`area`/geometry, `session_id`, `trace_id`, `subject_ref`, `response_max_chars`, and our `status`/`cause` — is forced into `metadata`. That is **14 of 16 available keys used on day one**, with `subject_ref` alone flattened into four keys because `metadata` cannot nest. The heavy reliance on `metadata` to carry the core exchange is the tell: the pattern is being retrofitted, not fitted.

### 5.3 `metadata` is buried, constrained, and needs a custom client anyway

`metadata` values are **strings capped at 512 characters**, with a **16-key limit**, no lists, no nesting, no numbers — so a polygon cannot travel and geometry degrades to a lossy `"lon,lat"` string. Worse, `metadata` is designed to be echoed back and logged freely by SDKs and proxies, which is exactly wrong for `subject_ref` (a secret) and location (personal) — the adapter must strip them on the way out. And no stock SDK reads `metadata` to drive control flow, so the client needs custom code regardless — which removes the reason to be compatible at all.

### 5.4 Custom reason codes cannot survive, so the caller cannot change course

`error.code` is a fixed vendor enum with no `provider_unavailable`, no `timeout`, no `domain_unmapped`. Our `cause` codes exist precisely so the API user can change course based on *why* a turn ended — and the format has nowhere to carry them except `metadata`, where no SDK will look.

### 5.5 Two of five outcomes become invisible

`no_match` and `needs_clarification` both serialise as `status: "completed"` — identical to a real answer on the wire. Only `metadata.oan_status` distinguishes them, and no SDK reads it. On voice this matters: the caller cannot tell that the conversation is waiting on the farmer. This loss is unavoidable in the format, whatever the adapter does.

### 5.6 The format assumes stored state; the DSS stores nothing

Three of the four Responses endpoints — `GET`, `cancel`, `DELETE` — require server-side storage the DSS does not have, so all three return `404`. `store: true` and `previous_response_id` — both defaults in several SDKs and multi-turn helpers — return `400`, which a client following normal OpenAI patterns hits on its second turn.

### 5.7 The adapter is not small, and one detail silently corrupts citations

Five native DSS events must be synthesised into up to fourteen Responses events per turn, with a monotonic gapless `sequence_number`, echoed constant fields, and buffered text so citation spans can be computed. Those spans are **character offsets**, and the spec does not say what a character is — code points, UTF-16 units, or bytes disagree, and a naive Go implementation would emit bytes. Get it wrong and every citation lands on the wrong words, in exactly the Indian languages this system serves. That is the most likely bug in a real implementation, and it exists only because of the format.

**Where the good idea is kept.** The one genuine advantage — image and text in a single JSON request — is worth stealing without the rest, and does not require this surface. The native `analyze-image` endpoint can accept an image inline in the JSON body; the format's own image path becomes an SSRF hole (`image_url` fetching `169.254.169.254`) unless restricted to uploaded `file_id`s, at which point it is no better than multipart.

**Reconsideration.** This rejection is reopened only if an external, SDK-consuming client genuinely appears on this boundary — and even then §4.6 applies: it is served by an adapter in front of the DSS, not by replacing this contract.

---

## 6. Follow-up Actions

- **[Owner: DSS CODE OWNERS]** Implement the native contract in `entrypoint/` (the interface choice this ADR settles) and keep `docs/api-contracts/api-contract.md` as the authoritative field-level reference.
- **[Owner: DSS CODE OWNERS]** Enforce `ref` as a secret (`SecretStr`) that is never inspected, resolved, persisted, or logged; enforce that `geometry` is never written to the sinks.
- **[Owner: DSS CODE OWNERS]** Publish the full `cause` list per `status`, currently open (`api-contract.md` §8).
- **[Owner: Experience-layer leads]** Execute the per-deployment migration from `stream_chat_messages` for each current deployment per `api-contract.md` §7, including the `[lon, lat]` coordinate reversal.
- **[Owner: OAN DPG steward]** Update `docs/DSS_ARCHITECTURE.md` §8.3 to record REST + SSE as the chosen interface, and update the CLAUDE.md "Build & Run" entrypoint note in the same change.

---

## 7. Notes

- This ADR settles the interface-shape question that `docs/DSS_ARCHITECTURE.md` §8.3 and CLAUDE.md list as undecided (REST/gRPC/in-process). Update both when this ADR is accepted.
- gRPC was rejected — protobuf toolchains for little gain inside one subnet. An in-process call was rejected — it contradicts the separate-container model. Voice barge-in would reopen the transport question.
- The Responses evaluation is preserved in full at `docs/api-contracts/open-responses-api.md` so the cost stays known if the question is raised again; a Postman collection for the native contract is at `docs/api-contracts/dss-turns-stream.postman_collection.json`.
- Retries, image-turn envelope mapping, `TurnHistoryEntry` fields, and telemetry retention/access remain open (`api-contract.md` §8) and are not settled by this ADR.
