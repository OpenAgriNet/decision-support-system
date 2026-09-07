# `/v1/turns` — what was built, what the merge changed

**Issue:** #87 · **Branch:** `feat/87-api-contracts` · **Date:** 2026-09-07
**Companion to:** [`87-v1-turn-api.md`](./87-v1-turn-api.md) (the spec)

A review companion, in two parts:

- **Part A** — does the implementation actually follow the API contract, checked
  field by field rather than asserted.
- **Part B** — what had to change to merge with `main`, and why each choice was
  made.

**State:** 376 tests · 99% coverage · all three CI gates green.

```bash
DSS_STUB_LLM=true uv run uvicorn --factory dss.entrypoint.app:create_app --port 8077
```

---

# Part A — Does it follow the contract?

Two documents describe the wire:

- `docs/api-contracts/openapi.yaml` — the machine-readable spec, **authoritative**
- the single-endpoint proposal (Design Council, Version 1) — the prose

They disagree in a few places. Where they do, the code follows the spec. Every
result below comes from diffing the live Pydantic models against the proposal's
field lists, not from reading the code.

## A.1 Request — 8 of 8 objects match exactly

| Object | Result |
|---|---|
| `context` | ✅ `id`, `version`, `transactionId`, `messageId`, `timestamp`, `sessionId` |
| `message` | ✅ `input`, `userContext`, `attributes` |
| `attributes` | ✅ `sourceLanguage`, `targetLanguage`, `channel`, `location`, `response` |
| `location` | ✅ `region`, `area`, `geometry` |
| `response` | ✅ `maxCharacters` |
| `userContext[]` | ✅ `type`, `userId` |
| `input[]` | ✅ `role`, `content` |
| `content[]` | ✅ `type`, `text` |

No missing keys, no extra keys. Three things a key-diff cannot show:

**`content[].type` is narrower than the contract.** The proposal declares a
closed enum of `text | image`; only `text` is implemented. An image turn is
rejected with `422` rather than mishandled, but the variant is missing.

**`geometry.coordinates` differs, and the code is right.** The proposal's example
is `[[72.93, 22.56]]` — double-nested, which is invalid for `type: "Point"`
under RFC 7946. The code sends the flat pair.

**`attributes.domain` and `execution.allowedInteractions`** appear only in the
proposal's image example and are not implemented. §2's own rules forbid them:
*"the request does not contain … caller instruction that changes DSS policy."*

## A.2 Response — 3 differences, 2 of which the proposal disagrees with itself on

| Difference | Verdict |
|---|---|
| `context` has `traceId` + `sequenceNumber`, not `transactionId` | **The proposal contradicts itself.** §3's JSON block shows `transactionId`, but §3.1's event example, all four §4 examples, and §1.1's prose (*"injects the resulting traceId into responses and SSE events"*) all say `traceId`. `openapi.yaml` requires `traceId` and does not list `transactionId`. The code follows those. `sequenceNumber` is required by §3.1 on events and absent in JSON mode, which is correct. |
| `error.retryAfterSeconds`, not `retry_after_seconds` | **The proposal is internally inconsistent** — that one field is snake_case inside an otherwise camelCase envelope. `openapi.yaml` says `retryAfterSeconds`. |
| ~~`content[].sourceIds`, not `annotations`~~ | **Closed.** See §A.7 — a per-block citation is an annotation spanning the whole block, and the contract's own example settles the offset unit. |

## A.3 Seven conformance gaps closed

These were real defects. A client generated from `openapi.yaml` could not parse
the first answered turn, because three `required` fields were absent.

| Was | Now |
|---|---|
| `outcome.confidence` absent (**required**) | present; a stub supplies a number per status |
| `outcome.cause` omitted when null (**required**, nullable) | always emitted, via an explicit serializer |
| `message.error` absent | `TurnError` — `code`, `message`, `retryable`, `retryAfterSeconds` |
| `envelopeVersion` + `dssRelease` (**illegal** — `additionalProperties: false`) | one `context.version` |
| `responseMessageId` | `resMessageId` |
| `sequenceNumber` started at 0 | starts at 1 |
| `traceId` minted from `traceparent`, body value rejected | `traceId` **is** the caller's `transactionId`, now required |

**Two of those seven were not on the original list.** They were found by
`tests/conformance/v1/test_against_openapi.py`, which loads `openapi.yaml` and
validates rendered responses with `jsonschema`:

- `envelopeVersion` was *illegal*, not merely misnamed — `RequestContext` sets
  `additionalProperties: false`.
- Response `messageId` is required, but was dropped whenever the caller omitted
  one. The transport now mints it.

**The reusable lesson.** Pydantic's `exclude_none=True` silently drops any
`null`, so **every field the contract marks `required` *and* nullable is a latent
bug of the same shape.** `outcome.cause` slipped past several hand-written
conformance tests because each asserted what *we understood* the contract to say.
Validating against the spec itself catches the whole class.

## A.4 Four gaps still open

| Contract | Code | Blocked on |
|---|---|---|
| `401 · 403 · 502 · 504` | not emitted; `406 · 413 · 415` added instead | **the auth posture** |
| `content[].type` includes `image` | `text` only | the attachment service contract |
| `tracestate` | not read | nothing — no behaviour is specified for it |

## A.5 Decisions needed

1. **Confirm `startIndex` is code points** — the contract's own example implies
   it (§A.7) but never says so. An example is weaker than a rule, and a JS client
   counting UTF-16 units would diverge on emoji.
2. **`401`/`403` — reserve, implement, or make pluggable?** The contract asks for
   codes that ADR-0002 §2.3 says cannot happen, §1.1 says the DSS performs no
   authentication, and §4's example nevertheless sends a `Bearer` token.
3. **`502`/`504` — ever really returned?** They are defined "before streaming
   began", but JSON mode never begins streaming, and the proposal's own
   provider-outage example returns `200`.
4. **Should `406`/`413`/`415` join the contract?** They cover cases the contract
   requires handling but names no code for: an unsatisfiable `Accept`, a body
   over the size cap, a wrong `Content-Type`.
5. **What does `confidence` mean per status?** A refusal's 98 and an answer's 92
   are not the same measurement.

## A.6 For the Design Council draft

The proposal is going to review as Version 1 with contradictions `openapi.yaml`
has already settled. Regenerating §3 from the spec would remove them:

- §3's prose declares a two-axis `outcome.status` (`completed`/`failed`) plus
  `outcome.kind`. §3.0, all four §4 examples, and `openapi.yaml` implement a
  **single** `status` carrying domain values. There is no `kind` anywhere but
  that paragraph.
- §3's response JSON shows `transactionId` where everything else says `traceId`.
- §2's rules require `context.traceId` on the *request*; no such field exists —
  the request carries `transactionId`.
- §1.1 says `Authorization: No`; §1.3 defines `401`/`403`; §4 sends a `Bearer`
  token.
- §2's `input` array is not valid JSON (`{{`, a missing brace, a trailing comma).
- §3 writes `"sourceId/sourceName"` as one field name with a slash, which reads
  like a typo for two fields.
- §3.2 promises *"structured claims, missing, and refused records"*; none appear
  in any schema or example.
- §4's image example breaks the envelope: `ts` and `conversationId` instead of
  `timestamp` and `sessionId`, `inputText`/`inputImage` instead of the §2.0
  enum, and `response` outside `attributes`.

## A.7 Citations — how the gap closed without a decision

The blocker looked structural: `Annotation` marks `startIndex` and `endIndex`
**required**, so `annotations` cannot be emitted without offsets, and no
component computes sub-sentence spans.

Two observations resolved it.

**A per-block citation *is* an annotation spanning the whole block.** "This
sentence came from that source" means `startIndex: 0`, `endIndex: len(text)`.
That satisfies the required offsets without inventing spans nothing computed, and
it needs no domain change — `mapping._annotations()` synthesises them from
`TextBlock.source_ids`. When real sub-span citations arrive, `TextBlock` grows an
annotations field and the mapping stops synthesising.

**The contract's own example settles the unit.** For
"इस सप्ताह आनंद मंडी में गेहूं का भाव ₹2,275 प्रति क्विंटल है।" §3 gives
`end_index: 61`. That sentence is **61 code points, 61 UTF-16 units, and 151
bytes**, so bytes are ruled out and the remaining two agree for Devanagari.

A rendered Devanagari response now validates against `openapi.yaml` with zero
errors, and three tests pin the behaviour: the span covers the whole block, a
block citing nothing carries no annotations, and the offsets are code points
rather than bytes.

`transactionId` was also added to the response context — the proposal's §3
carries it, `ResponseContext` allows additional properties, and it costs one
echoed field.

**Nothing in this touched `main`.** `schema.py` and `mapping.py` do not exist on
`main`; the only shared file, `core/shared/models.py`, was not modified at all.

---

# Part B — Merging with `main`

## B.1 What happened

`main` merged provider discovery (`748c218`, PR #9). That brought **real
moderation and intent** — implementations this branch had only stubbed. It also
brought `ports/llm.py`, `orchestration/turn.py`, `orchestration/envelope.py`,
`config/settings.py`, and ADRs 0003–0005.

At that point `main` was 51 commits ahead and this branch 23 ahead, with 15
overlapping files.

**A rebase was attempted first and abandoned at step 7 of 23.** It stopped on
`ports/llm.py`, and the reason matters: that is not a text conflict. Five modules
on `main` import it, so resolving it meant *deciding* where the LLM interface
lives — and three more decisions of that kind were queued behind it. Carrying a
rebase through would have meant answering the same design questions up to 17
times. A merge resolves them once, and every commit on both sides survives.

## B.2 The resolution policy

The conflict in `core/shared/models.py` looked total — both sides had rewritten
the file end to end. But the two halves turned out to be **disjoint concerns
sharing a filename**:

- `main` owned the **inbound** envelope: the turn as it arrives.
- this branch owned the **outbound** types: what a turn produces. `main` had
  none of them.

So both sides were taken in full, and only genuine duplicates were dropped.

```
core/shared/models.py
├── from main   ReferenceToken, UserDetails, ConversationMessage,
│               Geometry, Location, UserTurn, BCP-47 validator
│
├── ─── comment marking the seam ───
│
└── from here   TurnContext, TurnStatus, Cause, SourceKind, Source,
                TextBlock, RefusalBlock, OutputContent, TurnOutcome,
                TurnStarted, Claim, TurnFinished, TurnEvent
```

## B.3 File-by-file

| File | Resolution | Why |
|---|---|---|
| `core/shared/models.py` | both halves, seam commented | disjoint concerns (§B.2) |
| `core/intent/*`, `core/moderation/*` | **`main` wins entirely** | it has real implementations; ours were `STUB(#82)`/`STUB(#83)`. Applying ours would have replaced working code with a word list |
| their tests | **`main` wins entirely** | they test `main`'s implementations |
| `ports/llm.py` | **`main`'s stays** | five modules import it — `core/intent/service.py`, `core/moderation/service.py`, `orchestration/turn.py`, `adapters/llm/pydantic_ai_provider.py`, `adapters/llm/__init__.py` |
| `core/shared/llm.py` | **deleted** | our duplicate of the above. Signature was identical; only the name differed (`LLM` → `LLMProvider`) |
| `config/settings.py` | **`main`'s, extended** | pydantic-settings with `env_prefix="DSS_"` — what our hand-rolled `_env` helper approximated. The HTTP and evidence knobs moved into it |
| `entrypoint/settings.py` | **deleted** | superseded by the above |
| `pyproject.toml` | hand-merged | `main`'s runtime deps plus `fastapi`/`uvicorn`, which graduated from dev-only now that ADR-0006 makes FastAPI the committed entrypoint |
| `uv.lock` | **regenerated**, not hand-merged | a hand-merged lockfile is a guess |
| `tests/conftest.py` | ours | `main`'s is a one-line docstring |
| `AGENTS.md`, `CLAUDE.md` | both lines | one added the HTTP framework, the other anyio |
| ruff `per-file-ignores` | **`main`'s adopted** | `adapters/**` needs the TID251 exemption, because `pydantic_ai_provider.py` legitimately imports the framework |
| coverage gate | **`main`'s posture** | reported locally, gated in CI at 85% — our `--cov-fail-under` in `addopts` made every local subset run fail |

Four of our types were duplicates and were deleted in favour of `main`'s:

| Ours | `main`'s |
|---|---|
| `Point(lon, lat)` | `Geometry(coordinates: list[float])` |
| `HistoryEntry` + `Role` | `ConversationMessage` |
| `Channel` enum | `channel: str` — the enum now lives in the wire schema, where it validates |
| `ANONYMOUS` constant | `UserDetails.user_id: str \| None` — `None` means unattributed |

## B.4 Three consequences that were design decisions

**1. The runner delegates instead of sequencing.** `CoreRunner` now calls
`orchestration/turn.py::run_turn`, so intent and moderation genuinely run **in
parallel** (ADR-0003) rather than one gating the other. Our sequential version
was strictly worse.

**2. A moderation `Outcome` is not a turn status.** `main`'s `Outcome` has four
values; the contract's `status` has six, and they do not line up:

```
Outcome.REJECT    -> TurnStatus.REJECTED
Outcome.CLARIFY   -> TurnStatus.REQUIRES_INPUT     ← would have been lost
Outcome.NO_MATCH  -> TurnStatus.NO_MATCH
```

`CLARIFY` means the DSS understood and needs more from the farmer. Our
pre-merge runner mapped every non-`PROCEED` outcome to `rejected`, which told a
caller "we refused you" when the truth was "we need one more detail".

**3. Refusal wording moved out of the runner.** It now comes from
`core/moderation/messages.py`. The runner does not write prose.

Also: `TurnContext` dropped `transaction_id`. `trace_id` already *is* it, and
`UserTurn` keeps its own copy for the Provider hop. Recording both in the
evidence sink would have been the same fact twice.

## B.5 `ReferenceToken` survived — an open question just closed

`main` ships `ReferenceToken(value, issuer, expires_at)` with "an expired token
counts as absent" implemented, and `UserDetails.reference` carrying it.

Earlier in this branch's design review, decision **A.2 dropped `ref`** on the
grounds that the single-endpoint proposal removed it, with the stated
consequence that *no Provider could be invoked on a farmer's behalf in v1*. That
question had been flagged as blocking.

**It is answered: the token ships.** A.2 is void, and on-behalf-of Provider calls
are in scope.

## B.6 One property lost, stated plainly

Our `Point` held **named floats**, which made a reversed coordinate pair
*unrepresentable* inside the hexagon — the `[lon, lat]` ordering existed in one
mapping function and nowhere else. `main`'s `Geometry` keeps the GeoJSON
positional pair, so that guarantee is gone.

A range check cannot recover it: for Anand, `72.93` and `22.56` are **both** a
valid latitude and a valid longitude, so a swapped pair produces a legal
`Geometry`. The mapping test is now the only guard.

Partial recovery, if wanted: add read-only `lon`/`lat` properties to `Geometry`
so every *read* downstream is named even though construction stays positional.

## B.7 A bug the merge exposed, and why 376 passing tests missed it

After the merge every turn came back `moderation_unavailable`, including turns
that should have been answered.

Cause: `StubLLM` had no canned answer for moderation's `LlmModerationVerdict`
schema, so it raised `KeyError`; moderation's LLM policies use
`fail_mode=CLOSED`, which correctly converts a moderation failure into a refusal.

**The suite did not catch it.** Every test injects its own fake provider, so only
`create_app()` — the real composition root — takes that path. The one test that
did drive `create_app()` asserted that the evidence files appeared. They did:
**a refused turn writes them too.**

Both fixed: the stub answers moderation's schema, and that test now asserts the
outcome rather than the side effect. Found by running the server and reading the
response, which no amount of green tests substitutes for.

## B.8 Verification

Three CI gates, run exactly as CI runs them:

```bash
uv run ruff check .                                                  # OK
uv run ruff format --check .                                         # OK
uv run pytest --cov=src/dss --cov-report=term-missing --cov-fail-under=85
#   376 passed · 99.15%
```

And the running service:

```
answered   {'status': 'answered', 'confidence': 92, 'cause': None}   2 claims, 1 source
traceId    txn_9f2c1a8e                        ← echoes the caller's transactionId
streaming  turn.created 1 · claim.completed 2, 3 · turn.completed 4
telemetry  moderation proceed · intent Market · channel 2
evidence   var/evidence/{telemetry,turns}.jsonl written
validation PASS against openapi.yaml, zero errors
```

**Caveat worth knowing:** with `DSS_STUB_LLM=true` the canned provider reports no
violation, so `delete-command` and every other **LLM-evaluated** policy lets the
turn through. Deterministic policies (`profanity-filter`) still apply. The dev
server looks like it is moderating when it is only half moderating. A real
refusal needs a real provider, or a test with its own fake —
`tests/integration/orchestration/test_core_runner.py` does that.

## B.9 What is still stubbed

| Marker | Where | Replaced by |
|---|---|---|
| `STUB(#81)` | `orchestration/stub_runner.py` | nothing — test-only, a third `TurnRunner` for the port test |
| `STUB(#83)` | `adapters/llm/stub.py` | already built: `pydantic_ai_provider.py`, wired when `DSS_STUB_LLM` is unset |
| `STUB(#84)` | `core/channel/service.py` | the response composer + reviewer |
| `STUB(#85)` | `adapters/sinks/memory.py` | a durable turn store |
| `STUB(#86)` | `orchestration/core_runner.py` | intent and composition reporting their own confidence |

## B.10 Next slice

Half the intended flow arrived with the merge:

```
intent ∥ moderation                ✅ run_turn, ADR-0003
discovery(intent.asks)             ⬜ exists on main; run_turn does not call it
plan(discovery, verdict, skills)   ⬜ the barrier — the highest-value case left
sufficiency(evidence, asks)        ⬜ plain code, no LLM, ever
compose(evidence, identity)        ⬜ STUB(#84) today
```

Discovery is permitted to cross the moderation barrier — a discovery query is
read-only and disposable; a provider invocation is not. So the gate is **"no tool
call before the verdict resolves"**, not "moderation runs first".

That barrier is testable with stubs today: a verdict that records when it was
awaited, an invocation stub that records when it was called, assert the ordering —
then reorder the runner and watch the test fail.
