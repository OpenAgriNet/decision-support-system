# DSS API Contract: single endpoint proposal

**Status:** Proposed for review

This proposal follows the structure of the current DSS API Contract. It uses one turn endpoint for text, image, streaming, and non-streaming interactions. File input is not yet supported (§2.3).

---

## 1. Transport

REST over HTTP. Server-Sent Events are used when the caller requests streaming.

| Operation | Method and path | Request | Response |
|---|---|---|---|
| Turn | `POST /v1/turns` | `application/json` | `application/json` or `text/event-stream` |
| Streamed turn | `POST /v1/stream/turns` | `application/json` | `text/event-stream` |

The request body contains typed input items. Text, image, and earlier conversation messages therefore use the same endpoint. The HTTP `Accept` header selects JSON or SSE without changing the operation.

The two operations take the same body and produce the same answer. They differ in when the answer leaves: `/v1/turns` sends the composed block once it is finished, `/v1/stream/turns` sends each piece as the composer writes it and then the finished block. A caller that cannot use pieces should use `/v1/turns` — asking the streaming route for `application/json` is a 406, not a buffered response.

### 1.1 Headers

| Header | Required | What it is |
|---|:--:|---|
| `Authorization` | No | DSS does not perform authentication/authorization |
| `Content-Type` | Yes | `application/json` |
| `Accept` | Yes | `application/json` by default; use `text/event-stream` for SSE. Required on `/v1/stream/turns` |
| `Content-Encoding` | No | `gzip`, when the request body is gzip-compressed |
| `traceparent` | No | Standard W3C trace context propagated by OpenTelemetry instrumentation |
| `tracestate` | No | Optional vendor-specific W3C trace state |

Conversation identity is carried as `context.sessionId`, not as a separate session header. The Experience owns conversation storage and supplies any earlier messages required for the turn.

DSS accepts gzip-compressed request bodies. `message.input` can carry the full turn history as text, so request size grows with conversation length; gzip keeps that cost off the network.

DSS middleware extracts `traceparent`, starts the DSS server span, and injects the resulting `traceId` into responses and SSE events. The trace ID remains the same across services. Span IDs record parent-child relationships, so the API does not define a separate parent-trace field.

### 1.2 Versioning

The major API contract is identified by the path: `/v1/turns`.

DSS always serves the latest deployed release that conforms to the `/v1` contract. The caller cannot select an older internal DSS release.

`context.version` is set by DSS in responses and events. It identifies the concrete DSS release that handled the turn. The release identifier may use `v1`, `v1.1`, semantic versioning, or a date, depending on the implementation.

A DSS release identified as `v1.1` must remain backward compatible with clients built against the earlier `/v1` behaviour. Additive optional response fields and new content variants are allowed when existing clients can safely ignore them. Removing a field, changing its type or requiredness, or changing established semantics requires a new major contract such as `/v2/turns`.

Each DSS release must pass the `/v1` conformance suite before deployment. Compatibility is established by tests, not by the release label alone.

### 1.3 Status codes

A valid turn that DSS handles returns HTTP 200, including a safe rejection, no match, or request for more information. The domain result is carried by `message.outcome`.

| Code | Meaning |
|---|---|
| 200 | DSS handled the turn; inspect the turn status and outcome |
| 400 | Malformed request or envelope |
| 401 | Missing or invalid workload authentication |
| 403 | Authenticated workload is not permitted to call DSS |
| 422 | Well-formed request that violates the contract |
| 429 | Concurrency or rate limit reached; may carry `Retry-After` |
| 502 | A required upstream service returned an invalid response before streaming began |
| 503 | DSS or a required dependency is unavailable before streaming began |
| 504 | A required dependency timed out before streaming began |

After an SSE stream has opened with HTTP 200, an execution or dependency failure is reported by a terminal `turn.failed` event. HTTP status cannot change after streaming starts.

---

## 2. Request

Every request has two top-level objects. `context` carries operational metadata used for message identity, correlation, and timestamps. `message` carries the DSS turn. Within the message, `input` contains the typed content to process and `attributes` contains the caller-supplied facts that qualify that input. Operational context is not model input.

```json
{
  "context": {
    "id": "api.dss.turn",
    "version": "2.0.0",
    "transactionId": "9f2c1a8e-4b70-4d31-9c55-6f2e0b1d7a44",
    "messageId": "7d41b9e0-52a6-4c18-8b73-1e9f0a4c6d22",
    "timestamp": "2026-08-26T06:12:04.918Z",
    "sessionId": "conv_8f3a1c"
  },
  "message": {
    "userContext": [
      {
        "type": "identity",
        "userId": "string"                      // canonical id, "anonymous" if unknown
      }
    ],
    "input": [
      {
        "role": "user",
        "content": [{ "type": "text", "text": "What is wrong with my crop?" }]
      },
      {
        "role": "assistant",
        "content": [{ "type": "text", "text": "Wheat is ₹2,275 per quintal at Anand mandi." }]
      },
      {
        "role": "user",
        "content": [{ "type": "text", "text": "And potato?" }]
      }
    ],
    "attributes": {
      "sourceLanguage": "string",              // BCP 47
      "targetLanguage": "string",               // BCP 47
      "channel": "web|whatsapp|voice|sms",
      "location": {                              // optional
        "region": "string|null",                // ISO 3166-2, e.g. "IN-GJ", "IN-CH"
        "area": "string|null",                  // local name, e.g. "Anand"
        "geometry": {
          "type": "Point",
          "coordinates": [72.93, 22.56]         // [lon, lat]
        }
      },
      "response": {
        "maxCharacters": 1200
      }
    }
  }
}
```

Rules:

- `context.id`, `context.timestamp`, `context.sessionId` and `context.transactionId` are required.
- `message.userContext` is an array of typed identity facts, following the same typed-variant pattern as `message.input`. It carries no phone, name, email, or JWT claims — only opaque, per-turn facts DSS is allowed to see. New facts (e.g. a Provider reference token) are added as new typed variants, not as untyped keys.
- `message.input` contains the current user message and any earlier messages that the Experience chooses to supply.
- `message.attributes` contains the language, channel, location, and non-personal domain facts DSS may use when processing the input.
- `message.attributes.sourceLanguage` and `message.attributes.targetLanguage` use BCP 47 language tags.
- `message.attributes.channel` describes the Experience channel. Raw audio is transcribed by the Experience before DSS is called.
- The request does not contain a person identifier, authorization artifact, or caller instruction that changes DSS policy.
- Location attributes contain only the minimum non-personal information needed for the turn.
- The `Accept` header selects streaming. There is no streaming flag in the request.
- The *route* selects whether the answer is released as it is written. `/v1/stream/turns` does, `/v1/turns` does not — the request body is identical either way.
- Unknown request fields are rejected where the schema declares a closed object. Clients must ignore unknown optional response fields to remain compatible with later `/v1` releases.

### 2.0 Types used in this request

- `message.attributes.channel` — closed enum: `web` | `whatsapp` | `voice` | `sms`.
- `message.attributes.sourceLanguage` / `targetLanguage` — BCP 47 language tag (e.g. `en`, `hi`), not an enum; validated against the list of languages DSS publishes. Adding a supported language is not a contract change.
- `message.attributes.location.region` — ISO 3166-2 subdivision code (e.g. `IN-GJ`, `IN-CH`), validated against the ISO 3166-2 list.
- `message.userContext[].type` — closed enum for v1: `identity`. New facts are added as new typed variants, not new keys on `identity`.
- `message.input[].content[].type` — closed enum for v1: `text` | `image`.
- `attachment.url` — a pre-signed read URL for the deployment's own blob store. Validated against the deployment's configured host allowlist; any other host is rejected with `422`.

### 2.1 Earlier messages

Earlier user and assistant messages use the same typed input array:

```json
{
  "role": "assistant",
  "content": [
    {
      "type": "text",
      "text": "Which crop are you asking about?"
    }
  ]
}
```

DSS reads only the messages supplied in the request. The Experience remains responsible for storage, retention, summarization, and user-visible conversation behavior.

### 2.2 Image input

An image is represented as a typed content item carrying a link:

```json
{
  "type": "image",
  "attachment": {
    "url": "https://blob.oan.internal/att/att_tomato_leaf_01?X-Amz-Expires=900&X-Amz-Signature=...",
    "mediaType": "image/jpeg",
    "sha256": "29c8e6a1c8102d3d7a2e6f21c1bd2fa96564d30d93a66fba211e50e7d7ecfb79"
  }
}
```

The bytes never pass through the Experience layer. The client app uploads directly to the blob store with a pre-signed `PUT` URL, then hands the object reference to the Experience, which mints a short-lived pre-signed read URL and sends that to DSS. DSS fetches the bytes from the URL.

- `attachment.url` — a pre-signed read URL for the deployment's own blob store. Not an arbitrary caller-supplied URL: DSS accepts only hosts on the deployment's configured allowlist and rejects anything else with `422`.
- `attachment.mediaType` — closed enum for v1: `image/jpeg`, `image/png`. Any other value is rejected with `422`.
- `attachment.sha256` — 64-character lowercase hexadecimal SHA-256 digest. DSS verifies the fetched bytes against it and rejects a mismatch with `422`.

An expired URL is a `422`. DSS does not retry an expired link or ask for a new one; the Experience mints the URL per turn.

**Scanning happens before the link is usable.** Upload triggers a scan in the blob store and the object stays quarantined until it passes. The Experience mints a read URL only for a clean object, so DSS never fetches unscanned bytes. DSS does not scan.

Size limits, expiry duration, the upload-URL grant, quarantine, and cleanup are defined by the attachment service contract.

### 2.3 File input — not yet supported

`file` is not part of the v1 `message.input[].content[].type` enum. This section documents the intended shape for a future proposal; it is not accepted by v1.

```json
{
  "type": "file",
  "attachment": {
    "url": "https://blob.oan.internal/att/att_soil_report_01?X-Amz-Expires=900&X-Amz-Signature=...",
    "mediaType": "application/pdf",
    "filename": "soil-report.pdf",
    "sha256": "62c268eba739c01b928dd5a0fbf0bace363e8dfc6d3f7f12fe784c3f255e6902"
  }
}
```

---

## 3. Response

The same canonical response object is used for non-streaming JSON and the terminal SSE event.

```json
{
  "context": {
    "id": "api.dss.turn",
    "version": "v1.1",
    "timestamp": "2026-09-01T09:30:02Z",
    "messageId": "msg_01KXYZ",
    "sessionId": "conv_8f3a1c",
    "traceId": "4bf92f3577b34da6a3ce929d0e0e4736"
  },
  "message": {
    "outcome": {
      "status": "answered",   // answered | rejected | no_match | requires_input | unavailable | partially_answered
      "cause": null,          // null | unsafe_illegal | provider_unavailable | domain_unmapped | ...
      "confidence": 92        // percentage, 0-100
    },
    "content": [
      {
        "type": "text",
        "text": "इस सप्ताह आनंद मंडी में गेहूं का भाव ₹2,275 प्रति क्विंटल है।",
        "annotations": [
          { "type": "url_citation", "url": "https://agmarknet.gov.in/...",
            "sourceId": "src_1", "sourceName": "Agmarknet", "startIndex": 0, "endIndex": 61 }
        ]
      }
    ],
    "sources": [
      { "id": "src_1", "name": "Agmarknet", "kind": "provider" }
    ]
  }
}
```

`message.outcome` carries the result of the turn:

- `message.outcome.status` is the domain result — `answered`, `rejected`, `no_match`, `requires_input`, `unavailable`, or `partially_answered`.
- `message.outcome.cause` is a machine-readable reason for the status, or `null` when the turn is answered.
- `message.outcome.confidence` is DSS's confidence in the answer, expressed as a percentage from 0 to 100. A safe rejection, no match, or request for more information is still an HTTP 200 turn; a dependency failure is reported as an `unavailable` outcome, with details in `message.error`.

### 3.0 Types used in this response

- `message.outcome.status` — closed enum: `answered` | `rejected` | `no_match` | `requires_input` | `unavailable` | `partially_answered`.
- `message.outcome.cause` — open, additive set. `null` when answered; otherwise a machine-readable string. Known values: `unsafe_illegal`, `provider_unavailable`, `domain_unmapped`. New causes may be added without a major version change — clients must treat an unrecognized value as an opaque string, not fail on it.

### 3.1 Streaming events

| Event | Meaning |
|---|---|
| `turn.created` | DSS accepted the turn and started execution |
| `claim.completed` | One reviewed content item, in the response shape, ready for presentation |
| `turn.completed` | The complete response; the authoritative terminal event |
| `turn.failed` | The canonical terminal turn failed |

Each event carries a monotonically increasing `sequenceNumber`. The terminal event is authoritative. The client does not infer success or failure from connection closure.

Example event:

```
event: claim.completed
data: {"context":{"id":"api.dss.turn","version":"v1.1","messageId":"msg_mandi_wheat_01","sessionId":"conv_8f3a1c","traceId":"9f2b7c1a487a9138e394d31b51134a61","timestamp":"2026-09-01T09:30:01Z","sequenceNumber":1},"message":{"content":[{"type":"text","text":"इस सप्ताह आनंद मंडी में गेहूं का भाव ₹2,275 प्रति क्विंटल है।","annotations":[{"type":"url_citation","url":"https://agmarknet.gov.in/...","sourceId":"src_1","sourceName":"Agmarknet","startIndex":0,"endIndex":61}]}]}}
```

### 3.2 Final event

The final event carries the complete response — context, outcome, content, and sources. Clients should store or render the final event rather than reconstructing it from earlier claim events.

### 3.3 Non-streaming

`POST /v1/turns` with `Accept: application/json` returns the canonical turn directly. No event-specific wrapper is used.

---

## 4. Examples

Every successfully represented and processed domain turn returns HTTP 200. The examples below show different domain and execution outcomes.

### Answered, streaming

```
POST /v1/turns
Authorization: Bearer <experience-workload-token>
Content-Type: application/json
Accept: text/event-stream
traceparent: 00-9f2b7c1a487a9138e394d31b51134a61-00f067aa0ba902b7-01

event: claim.completed
data: {"context":{"id":"api.dss.turn","version":"v1.1","messageId":"msg_mandi_wheat_01","sessionId":"conv_8f3a1c","traceId":"9f2b7c1a487a9138e394d31b51134a61","timestamp":"2026-09-01T09:30:01Z","sequenceNumber":1},"message":{"content":[{"type":"text","text":"इस सप्ताह आनंद मंडी में गेहूं का भाव ₹2,275 प्रति क्विंटल है।","annotations":[{"type":"url_citation","url":"https://agmarknet.gov.in/...","sourceId":"src_1","sourceName":"Agmarknet","startIndex":0,"endIndex":61}]}]}}

event: turn.completed
data: {"context":{"id":"api.dss.turn","version":"v1.1","messageId":"msg_mandi_wheat_01","sessionId":"conv_8f3a1c","traceId":"9f2b7c1a487a9138e394d31b51134a61","timestamp":"2026-09-01T09:30:02Z","sequenceNumber":2},"message":{"outcome":{"status":"answered","cause":null,"confidence":92},"content":[{"type":"text","text":"इस सप्ताह आनंद मंडी में गेहूं का भाव ₹2,275 प्रति क्विंटल है।","annotations":[{"type":"url_citation","url":"https://agmarknet.gov.in/...","sourceId":"src_1","sourceName":"Agmarknet","startIndex":0,"endIndex":61}]}],"sources":[{"id":"src_1","name":"Agmarknet","kind":"provider"}]}}
```

### Answered, released as it is written

`POST /v1/stream/turns`. Same turn, same finished answer — the difference is the `claim.delta` frames ahead of it. Abbreviated context blocks; every frame carries the same ones as above, with its own `sequenceNumber`.

```
POST /v1/stream/turns
Content-Type: application/json
Accept: text/event-stream

event: turn.created
data: {"context":{...,"sequenceNumber":1},"message":{}}

event: claim.delta
data: {"context":{...,"sequenceNumber":2},"message":{"content":[{"type":"output_text_delta","text":"इस सप्ताह आनंद मंडी में "}]}}

event: claim.delta
data: {"context":{...,"sequenceNumber":3},"message":{"content":[{"type":"output_text_delta","text":"गेहूं का भाव ₹2,2"}]}}

event: claim.delta
data: {"context":{...,"sequenceNumber":4},"message":{"content":[{"type":"output_text_delta","text":"75 प्रति क्विंटल है।"}]}}

event: claim.completed
data: {"context":{...,"sequenceNumber":5},"message":{"content":[{"type":"text","text":"इस सप्ताह आनंद मंडी में गेहूं का भाव ₹2,275 प्रति क्विंटल है।","annotations":[{"type":"url_citation","url":"https://agmarknet.gov.in/...","sourceId":"src_1","sourceName":"Agmarknet","startIndex":0,"endIndex":61}]}]}}

event: turn.completed
data: {"context":{...,"sequenceNumber":6},"message":{"outcome":{"status":"answered","cause":null,"confidence":92},"content":[...],"sources":[{"id":"src_1","name":"Agmarknet","kind":"provider"}]}}
```

What a consumer has to know:

- **Append, do not replace.** Concatenating every `claim.delta` text gives the `claim.completed` text exactly. A piece may end mid-word, mid-number, or mid-citation-marker — the third frame above splits `₹2,275`.
- **A delta carries no citations.** A block still being written has no end index for an annotation to span. Provenance arrives with `claim.completed`.
- **The terminal event is still authoritative.** Store it; do not reassemble it from the pieces you rendered.
- **Only a composed answer streams.** A refusal, a no-match, or a request for more information is a fixed reply, so those turns carry no `claim.delta`.
- **There is no retry after the first piece.** A failure from that point on arrives as `turn.failed` inside the already-open 200.
- **An unrecognised event name is safe to skip.** A consumer that ignores `claim.delta` still gets the whole answer from `claim.completed`.

### Rejected

```json
{
  "context": {
    "id": "api.dss.turn",
    "version": "v1.1",
    "messageId": "msg_rejected_01",
    "sessionId": "conv_8f3a1c",
    "traceId": "4a1f0a96fd9e4e01940ee6ec177b8328",
    "timestamp": "2026-09-01T09:31:00Z"
  },
  "message": {
    "outcome": {
      "status": "rejected",
      "cause": "unsafe_illegal",
      "confidence": 98
    },
    "content": [
      {
        "type": "refusal",
        "text": "I can only answer agriculture and livestock related questions."
      }
    ]
  }
}
```

### Provider outage

```json
{
  "context": {
    "id": "api.dss.turn",
    "version": "v1.1",
    "messageId": "msg_mandi_outage_01",
    "sessionId": "conv_8f3a1c",
    "traceId": "f54280ec6a7e44f9b6221265e4150ca1",
    "timestamp": "2026-09-01T09:32:00Z"
  },
  "message": {
    "outcome": {
      "status": "unavailable",
      "cause": "provider_unavailable",
      "confidence": 0
    },
    "content": [
      {
        "type": "text",
        "text": "The required market-price service is unavailable. Please try again shortly."
      }
    ],
    "error": {
      "code": "provider_unavailable",
      "message": "The required market-price Provider is unavailable.",
      "retryable": true,
      "retryAfterSeconds": 30
    }
  }
}
```

### No match, non-streaming

```json
{
  "context": {
    "id": "api.dss.turn",
    "version": "v1.1",
    "messageId": "msg_no_match_01",
    "sessionId": "conv_8f3a1c",
    "traceId": "a17c3e9018bc4e6382c078e09714e72b",
    "timestamp": "2026-09-01T09:33:00Z"
  },
  "message": {
    "outcome": {
      "status": "no_match",
      "cause": "domain_unmapped",
      "confidence": 88
    },
    "content": [
      {
        "type": "text",
        "text": "I could not find an approved agricultural capability for that request."
      }
    ]
  }
}
```

### Partial answer, refusal, and missing input

```json
{
  "context": {
    "id": "api.dss.turn",
    "version": "v1.1",
    "messageId": "msg_partial_01",
    "sessionId": "conv_8f3a1c",
    "traceId": "5b8d2f11c0a94e7d8f1a2b3c4d5e6f70",
    "timestamp": "2026-09-01T09:34:00Z"
  },
  "message": {
    "outcome": {
      "status": "partially_answered",
      "cause": null,
      "confidence": 74
    },
    "content": [
      {
        "type": "text",
        "text": "Potato grows best in well-drained sandy loam soil.",
        "annotations": [
          { "type": "url_citation", "url": "https://icar.example/...",
            "sourceId": "src_1", "sourceName": "ICAR Potato Guide", "startIndex": 0, "endIndex": 49 }
        ]
      },
      {
        "type": "refusal",
        "text": "I cannot help with gold prices."
      },
      {
        "type": "text",
        "text": "Which district should I check potato prices for?"
      }
    ],
    "sources": [
      { "id": "src_1", "name": "ICAR Potato Guide", "kind": "document" }
    ]
  }
}
```

The farmer-facing content is authoritative for presentation. Each content item carries its own type (text or refusal) and its own citations, so the Experience can route and render without parsing prose.

### Image question

```json
{
  "context": {
    "id": "api.dss.turn",
    "transactionId": "3e7a91c4-2d16-4f88-9a01-b5c7d2e40f13",
    "messageId": "msg_crop_image_01",
    "timestamp": "2026-09-01T09:35:00Z",
    "sessionId": "conv_crop_image_01"
  },
  "message": {
    "userContext": [
      { "type": "identity", "userId": "anonymous" }
    ],
    "input": [
      {
        "role": "user",
        "content": [
          {
            "type": "text",
            "text": "What disease is affecting this tomato leaf?"
          },
          {
            "type": "image",
            "attachment": {
              "url": "https://blob.oan.internal/att/att_tomato_leaf_01?X-Amz-Expires=900&X-Amz-Signature=...",
              "mediaType": "image/jpeg",
              "sha256": "29c8e6a1c8102d3d7a2e6f21c1bd2fa96564d30d93a66fba211e50e7d7ecfb79"
            }
          }
        ]
      }
    ],
    "attributes": {
      "sourceLanguage": "en",
      "targetLanguage": "en",
      "channel": "web",
      "response": {
        "maxCharacters": 800
      }
    }
  }
}
```

The route and controller remain unchanged. The typed content item determines which approved DSS capability handles the input.

---

## Open items

- Define the OpenAPI 3.1 schemas for the envelope, content items, outcome, sources, errors, and attachments.
- Agree the DSS release identifier convention used by `context.version`.
- Define the `/v1` conformance suite and deployment gate.
- Define workload authentication and authorization for every Experience deployment.
- Define attachment MIME types, size limits, and the quarantine/cleanup policy.
- Define the blob-store host allowlist DSS validates `attachment.url` against, and how it is configured per deployment.
- Define the pre-signed read-URL lifetime, and the upload-URL grant the client app uses.
- Define retry and duplicate-detection behavior for `messageId`.
