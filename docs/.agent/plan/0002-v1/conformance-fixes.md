# Making the code match the contract

**Issue:** #3 · **Status:** draft
**Contract of record:** `docs/api-contracts/openapi.yaml` on `origin/feat/10-planner-agent-poc`

## What this is

The server runs and 128 tests pass, but it does not match the contract. There
are **ten differences**. This plan lists each one, says why it is wrong, and says
what changes.

Three of them exist because I made a judgement call where the contract had not
been written yet, and the contract later went the other way. Those are the ones
worth reading first — they are not preferences any more.

## How the differences are grouped

The ten split into five batches, in the order they should be done. The first
batch needs no decisions from anyone. The last two do.

| Batch | What | Decisions needed | Rough size |
|---|---|---|---|
| **1** | Five field-level fixes | none | small |
| **2** | One rule that has to be inverted | none, but a test flips | small |
| **3** | Status codes | **yes** — two conflicts to settle | small once decided |
| **4** | Citations change shape | **yes** — one hazard to pin down | medium |
| **5** | Image input | **yes** — depends on another team | large |

---

## Batch 1 — five field fixes, no decisions

All five are in `schema.py` and `mapping.py`. Nothing in `core/` moves. Each gets
a tier-1 test.

### 1.1 `confidence` is missing

**Contract:** `outcome.confidence`, an integer 0–100. `openapi.yaml` marks it
**required**, and every worked example in the proposal has it.

**Code:** absent. I dropped it on the grounds that its meaning was undefined —
the proposal shows `98` on a refusal, `0` on an outage and `92` on an answer,
which are three different questions.

**Why that was wrong:** a required field cannot be omitted because its semantics
are unclear. The right response was to ask, not to leave it out. A client reading
`openapi.yaml` will expect it.

**Change:** add `confidence: int` to `TurnOutcome` and to the wire `Outcome`. The
stub composer supplies a fixed number.

**Still open:** what the number means per status. Worth asking, but it does not
block adding the field.

### 1.2 `error` is missing

**Contract:** `message.error` — `code`, `message`, `retryable`,
`retry_after_seconds`.

**Code:** absent. I dropped it because `code` duplicates `outcome.cause`.

**Why that was wrong:** the duplication is real, but it is the contract's
duplication to resolve, not mine to delete. Both documents carry the field.

**Change:** add a `TurnError` wire model, populated when the status is
`unavailable`. `retry_after_seconds` moves onto it from where I put it
(`outcome`).

### 1.3 Two version fields where the contract has one

**Contract:** one `context.version`, meaning *the DSS release that handled this
turn*.

**Code:** `envelopeVersion` **and** `dssRelease` — I split it because the request
example shows `"2.0.0"` and the response shows `"v1.1"`, which cannot be the same
field.

**Why that was wrong:** the split may still be the better design, but inventing
two wire fields to fix a contract ambiguity puts the code out of conformance
without settling anything.

**Change:** the response carries one `version` = the DSS release. Keep accepting
the request's `version` and ignore it.

**Still open:** whether the request's `version` means the envelope schema. Raise
it against the contract; do not encode a guess.

### 1.4 `responseMessageId` should be `resMessageId`

**Contract:** `resMessageId`.

**Code:** `responseMessageId` — I renamed it because `res` is an abbreviation.

**Why that was wrong:** the wire is not the place to improve someone else's
naming. A client matches the string.

**Change:** rename. Note in the contract review that `resMessageId` and
`messageId` hold the same value in §3's example with no stated difference — that
is a real question, separate from the name.

### 1.5 The sequence counter starts at the wrong number

**Contract:** `openapi.yaml` sets `sequenceNumber` minimum **1**.

**Code:** `turn.created` is `0`.

**Change:** start at 1. One line in `sse.py`, and the test named
"starts at zero" gets renamed and re-asserted.

---

## Batch 2 — the trace id comes from the body, not the header

**Contract:** `context.transactionId` is **required** on the request, and
`openapi.yaml` says `traceId` is *"echoing the request's
`context.transactionId`"*. `traceparent` starts the DSS server span.

**Code:** the opposite. `transactionId` is optional, `traceId` is minted from
`traceparent`, and a `traceId` in the body is **rejected** — I wrote a test
asserting that rejection, reasoning that a caller-supplied trace id lets someone
forge the key their own audit record is filed under.

**Why that was wrong:** the concern is real but the contract already decided. The
caller owns the correlation id; that is what `transactionId` is for.

**Change:**

- `transactionId` becomes required.
- `traceId` = `transactionId`, minted only if absent.
- `traceparent` is still read, for the span — not for the id.
- The test that asserts a body trace id is rejected **inverts**: it now asserts
  the body value is used.

A test flipping its assertion is worth flagging in review rather than quietly
editing, because it is a rule change and not a bug fix.

---

## Batch 3 — status codes. Needs a decision

**Contract:** `200 · 400 · 401 · 403 · 422 · 429 · 502 · 503 · 504`

**Code:** `200 · 400 · 406 · 413 · 415 · 422 · 429 · 503`

So four are missing and three were invented.

### The four missing ones each have a problem

**`401` and `403`** require workload authentication. But:

- The proposal's own §1.1 says `Authorization: No — DSS does not perform
  authentication/authorisation`.
- ADR-0002 §2.3 decided **no auth**, and that the DSS never returns 401 or 403.
- §4's example nevertheless sends `Authorization: Bearer …`.
- "Define workload authentication" is still listed as an open item.

**So the contract asks for codes that the contract also says cannot happen.**
Three options:

| Option | What it means |
|---|---|
| **Reserve them** | documented, never emitted. Honest today, zero code. |
| **Implement workload auth** | a real token check at the edge. Needs a new ADR — token format, issuer, rotation. |
| **Pluggable, off by default** | an authenticator port with a no-op default; adopters mount one. Behaves like "reserve" today. |

**`502` and `504`** are defined as *"before streaming began"*. But in JSON mode
streaming never begins, and the proposal's own provider-outage example returns
**`200`** with `status: unavailable`. So the same failure is `502` on one path and
`200` on the other, and the contract does not say which.

My reading: a dependency failure **always** lands in `outcome`, and `502`/`504`
are reachable only before the turn is admitted. If that is right they are nearly
unreachable and should be reserved, not implemented.

### The three I invented

`406` (unsatisfiable `Accept`), `413` (body over the size cap), `415` (wrong
`Content-Type`). Each is standard HTTP for a case the contract requires handling
but gives no code for. They should be **added to the contract**, not removed from
the code — but that is the contract's call, so they are flagged, not deleted.

**Nothing here is coded until these are answered.**

---

## Batch 4 — citations change shape. Needs a decision

**Contract:** each content item carries `annotations`, and each annotation is
`{type: "url_citation", url, sourceId, sourceName, start_index, end_index}`.

**Code:** each content item carries `sourceIds: ["src_1"]`. No offsets.

**Why I did that:** ADR-0002 §5.7 rejected the OpenAI Responses API partly
*because* character offsets have no defined unit. Code points, UTF-16 units and
bytes all disagree, and a wrong choice lands every citation on the wrong words —
in exactly the Indian-language scripts this system serves. So I cited per block
instead.

**Why that was wrong:** both the proposal and the adopted spec kept annotations.
My reasoning was a recommendation, and the contract did not take it. The code is
simply not conformant.

**The change:** add an `Annotation` model and emit `annotations`. That is
mechanical. The hazard is not.

**The decision that must be made first: what is one unit of `start_index`?**

| Choice | Consequence |
|---|---|
| Unicode code points | matches Python string indexing — free here, wrong for a naive JS client (which counts UTF-16) |
| UTF-16 units | matches JS — needs conversion on the Python side |
| Bytes | matches a naive Go client — worst for Indian scripts, where one character is 3 bytes |

Whatever is chosen has to be **written into the contract** and covered by a
conformance test in Devanagari and Tamil. Without that, this field is a bug
waiting for its first non-English turn.

`sourceId` and `sourceName` also both appear on an annotation, and §3 writes them
as one field with a slash (`"sourceId/sourceName"`), which looks like a typo for
two. Confirm before implementing.

---

## Batch 5 — image input. Needs another team

**Contract:** `content[].type` is a closed enum of `text | image` for v1. An image
is `{type: "image", attachment: {id, media_type, sha256}}` — a reference, never a
URL. The Experience receives, scans and stores the file before calling the DSS.

**Code:** `text` only. No image variant exists.

**What is missing beyond the schema:**

- what `attachment.id` resolves to, and who serves it
- MIME types, size limits, expiry, scanning, authorization — all listed as an
  open item in the proposal, owned by "the attachment service contract", which
  does not exist yet
- what the DSS does with an image once it has one, since no vision model is wired

**So this splits in two.** The **schema half** can be done now — accept the
variant, validate the `sha256` shape, reject `file` with a clear `422`. The
**behaviour half** cannot start until the attachment service exists.

Doing the schema half now is worth it: it makes the closed enum match the
contract, and an image turn fails with a useful message rather than
`extra_forbidden`.

---

## What is not changing, and why

`tracestate` is read by nobody. The contract lists the header but specifies no
behaviour for it, so there is nothing to implement. It travels with
`traceparent` through OpenTelemetry when that is wired.

---

## Order and what each batch unblocks

1. **Batch 1** — five fixes, no decisions, immediately. Closes the gaps a client
   reading `openapi.yaml` would hit first.
2. **Batch 2** — needs no decision but flips a test, so it wants a reviewer.
3. **Batch 3** — blocked on the auth question and the `502`/`504` reading.
4. **Batch 4** — blocked on the offset unit. Do not start before that is written
   down.
5. **Batch 5** — schema half after batch 1; behaviour half blocked on another
   team.

Batches 1 and 2 together bring the running server into conformance on everything
that is not itself an open contract question. That is the useful stopping point.

---

## Questions this plan needs answered

1. What does `confidence` mean, per status? (blocks nothing, but the number is
   meaningless until answered)
2. `401`/`403` — reserve, implement, or make pluggable?
3. `502`/`504` — reachable before admission only, or genuinely returned for a
   provider failure?
4. Should `406`/`413`/`415` be added to the contract?
5. `start_index` — code points, UTF-16 units, or bytes?
6. Is `"sourceId/sourceName"` two fields?
7. Does the request's `context.version` mean the envelope schema version?
8. `resMessageId` versus `messageId` — what distinguishes them?
