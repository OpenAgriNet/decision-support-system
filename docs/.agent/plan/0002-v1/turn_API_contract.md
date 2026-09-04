# 0003 — Build `POST /v1/turns` with a stub core

**Issue:** #3 · **Status:** in review · **Blocked by:** ADR-0003 (web framework)
**Aligned to:** session of 2026-09-03 · **Spec of record:** `docs/dss-design-v2.md`
**Full reasoning:** `turn_API_contract.detailed.md`

## What this builds

One working endpoint. Real plumbing, fake thinking.

The HTTP layer is real, the port is real, the runner returns canned data. No LLM,
no network, no business rules. `curl` it and get a valid answer.

**Why fake first:** most seams only break once assembled. Build the logic first
and you find out last.

**One rule:** signatures are final, bodies are throwaway.

---

## Naming

| Word | Means | Lives in |
|---|---|---|
| `Stub…` | ships, fake body, replaced later | `src/` |
| `Fake…` | test-only double | `tests/support/` |
| `STUB(#n)` | marker on a throwaway body — CI requires the issue number | anywhere |

Nothing else. No dummy, skeleton, scripted, mock, or recording.

Tests reuse the `Stub…` adapters instead of re-declaring them — a memory sink
already records, so a `Recording…` twin would be the same class twice.

---

## The flow

```
   POST /v1/turns
        │
        ▼
   1. HTTP        adapters/http/v1/ + entrypoint/
      JSON <-> objects, Accept, status codes, SSE frames
        │  UserTurn, TurnContext
        ▼
   2. PORT        ports/turn.py :: TurnRunner
      the only thing the HTTP layer may call
        │
        ▼
   3. RUNNER      orchestration/
      decides the order.  step A: canned.  step B: calls core
        │
        ▼
   4. PORTS OUT   ports/llm.py, ports/sinks.py          step B
        │
        ▼
   5. ADAPTERS    adapters/llm/, adapters/sinks/        step B

   Answers come back up as TurnEvent objects.
   Only hop 1 turns them into SSE or JSON.
```

Two arrows, opposite directions — that is the pattern:

- **Runtime:** HTTP → port → runner → core → port → adapter.
- **Imports:** adapter → port ← core. The port imports nothing.

---

## Two steps

The session scoped this as *"test the port, not the runner's internals."*

| | **A — now** | **B — next** |
|---|---|---|
| Proves | HTTP layer + port | runner + core wiring |
| Builds | hops 1, 2, 3 | hops 4, 5, real hop 3 |
| Runner | `StubRunner` — canned events | `CoreRunner` — calls three core functions |
| Core | none | `moderation`, `intent`, `channel` (stub bodies) |
| Cases | 1–3, 9–25, 28–30 | 4–8, 26–29 |

A green step A proves the transport. It does not yet prove the architecture — the
assembly cases are all step B.

---

## Files

`+` new · `A`/`B` step.

```
src/dss/
├── ports/
│   ├── turn.py                 + A   TurnRunner
│   ├── llm.py                  + B   LLM
│   └── sinks.py                + B   TurnSink, TelemetrySink
│
├── core/
│   ├── shared/models.py        + A   domain language
│   ├── moderation/             + B   agent — takes llm
│   ├── intent/                 + B   agent — takes llm
│   └── channel/                + B   agent — takes llm
│
├── orchestration/
│   ├── stub_runner.py          + A   StubRunner — canned, reads nothing
│   └── core_runner.py          + B   CoreRunner — orders the three core calls
│
├── adapters/
│   ├── http/v1/
│   │   ├── router.py           + A   route, headers, Accept, status codes
│   │   ├── schema.py           + A   wire models — field names live here
│   │   ├── mapping.py          + A   wire <-> domain, pure
│   │   ├── sse.py              + A   frames + sequence numbers
│   │   └── problem.py          + A   4xx / 5xx bodies
│   ├── llm/stub.py             + B   StubLLM — canned answers
│   └── sinks/
│       ├── memory.py           + B   MemoryTurnSink
│       └── stdout.py           + B   StdoutTelemetrySink
│
└── entrypoint/
    ├── app.py                  + A   ASGI app, startup, readiness
    ├── composition.py          + A   the only file naming concrete classes
    └── settings.py             + A   caps, release id

tests/
├── support/fakes.py            + A   FakeRunner
├── support/builders.py         + A   a_turn(), a_body()
├── unit/adapters/http/v1/      + A   mapping + sse
├── unit/core/                  + B
├── integration/entrypoint/     + A   ASGI client + FakeRunner
├── integration/orchestration/  + B   real runner, stub adapters
├── conformance/v1/             + A   contract examples, byte-for-byte
└── e2e/                        + A   one smoke test
```

`v1/` is a folder because the version is in the URL. A `/v2` envelope is a new
folder, not an edit.

**One job each, so one reason to change each.** `router.py` owns HTTP rules ·
`schema.py` + `mapping.py` own the envelope · `sse.py` owns frames and sequence
numbers · `composition.py` owns which concrete class · `orchestration/` owns
**the order** · `core/*/service.py` owns one rule each · `adapters/` translate.

Two checks that keep it honest:

- **The runner has no `if` with business meaning.** It branches on values other
  modules decided. The moment it grows `if turn.channel == "voice"`, a rule
  escaped `core/`.
- **Streaming lives in the HTTP layer, not the runner** (session decision). The
  runner produces events; it never frames, counts, or negotiates. Same reason the
  port returns an iterator instead of taking a push-sink.

---

## The port

```python
# src/dss/ports/turn.py
class TurnRunner(Protocol):
    def run(self, turn: UserTurn, ctx: TurnContext) -> AsyncIterator[TurnEvent]: ...
```

One method. Streaming vs JSON never crosses it — that is hop 1's business. SSE
forwards each event; JSON drains and renders the last one.

Three things satisfy it on day one: `StubRunner`, `FakeRunner`, later
`CoreRunner`. Three implementations is the evidence the port inverted something
instead of mirroring one caller.

---

## Step A — the stub runner

```python
# src/dss/orchestration/stub_runner.py — STUB(#81)
class StubRunner:
    async def run(self, turn: UserTurn, ctx: TurnContext) -> AsyncIterator[TurnEvent]:
        yield TurnStarted()
        for block in _ANSWER:
            yield Claim(content=block)
        yield TurnFinished(
            outcome=TurnOutcome(status=TurnStatus.ANSWERED),
            content=_ANSWER,
            sources=_SOURCES,
        )
```

It reads `turn` for nothing — deliberately. If the HTTP layer passes its cases
against this, the HTTP layer is proven independent of everything below it.

```python
# entrypoint/composition.py — the one place concrete classes are named
def build_runner(settings: Settings) -> TurnRunner:
    return StubRunner()        # step B: CoreRunner(llm=..., turns=..., telemetry=...)

# entrypoint/app.py
def build_app(settings: Settings) -> ASGIApp:
    return mount(v1_router(runner=build_runner(settings), settings=settings))
```

`router.py` receives a runner and never builds one. That is what makes it
testable against a fake.

---

## Step B — the core runner

Three core functions. All three are **agents** per the session's split, so all
three take the LLM port and are awaited.

```python
# src/dss/orchestration/core_runner.py
class CoreRunner:
    def __init__(self, *, llm: LLM, turns: TurnSink, telemetry: TelemetrySink) -> None: ...

    async def run(self, turn, ctx):
        yield TurnStarted()
        self._turns.opened(ctx, turn)

        screening = await screen(turn, llm=self._llm)
        if screening.outcome is Outcome.REJECT:
            yield self._close(ctx, TurnStatus.REJECTED, screening)   # no claims
            return

        intent = await recognise_intent(turn, llm=self._llm)
        if intent is None:
            yield self._close(ctx, TurnStatus.NO_MATCH, ...)
            return

        answer = await compose(turn, intent, llm=self._llm)
        for block in answer.content:
            yield Claim(content=block)                               # streams as produced
        yield self._close(ctx, TurnStatus.ANSWERED, answer)
```

`self._telemetry.stage(...)` after each step; omitted above for width.

| Function | Stub body returns | Real version |
|---|---|---|
| `screen` | `PROCEED`, or `REJECT` if the query hits `_DENY` | #82 policy evaluator |
| `recognise_intent` | whatever `StubLLM` holds, or `None` under `CONFIDENCE_FLOOR = 0.6` | #83 real prompt |
| `compose` | two text blocks + one source | #84 composer + reviewer |

`_DENY` is a word list, not a model, so the reject path is deterministic on day
one. It is the most obviously fake thing here, on purpose.

`CONFIDENCE_FLOOR = 0.6` is a **real** rule and survives. Its test never changes.

**Why these three.** The session split components into agents (intent,
moderation, planner, response composer) and plain code (tool, provider, skill
discovery). These three are agents, and they cover an early exit, a `None` path
with a real threshold, and multi-claim streaming. A discovery function adds
nothing they don't already prove.

**Two sinks, not one** — `dss-design-v2.md` §3, and the failure rules are
opposite:

| Port | Holds | Required | If it raises |
|---|---|---|---|
| `TurnSink` | the turn record — query, answer, refusals. Farmer content, on purpose | yes, it's the audit trail | **the turn fails** |
| `TelemetrySink` | stage, timing, outcome. **Never** farmer content | no, stdout default | swallowed |

---

## Mocking

One double. Hand-written — a `MagicMock` answers a method you renamed in the
Protocol, so the test keeps passing after the contract moved.

```python
# tests/support/fakes.py
class FakeRunner:
    def __init__(self, events, *, fail_after=None): ...
    async def run(self, turn, ctx):
        for i, e in enumerate(self._events):
            if i == self._fail_after:
                raise ProviderUnavailable("stub")
            yield e
```

Everything else reuses the shipped stubs:

```
mapping.py, sse.py      <- nothing. pure functions.
core/*/service.py       <- StubLLM
orchestration/          <- StubLLM + MemoryTurnSink + StdoutTelemetrySink
http + entrypoint       <- FakeRunner
e2e                     <- the real app, wired to the stubs
```

**Rule: no test uses two layers' doubles at once.** A test needing `FakeRunner`
*and* `StubLLM` is testing two things — split it.

---

## Cases that must pass

| # | Case | Expected | Step |
|---|---|---|---|
| 1 | Answered, streaming | `turn.created` 0 · `claim.completed` 1, 2 · `turn.completed` 3 | A |
| 2 | Answered, JSON | `200`, terminal body only, no `sequence_number` | A |
| 3 | Same turn both ways | identical `message` object | A |
| 4 | Devanagari | contract example byte-for-byte | A |
| 5 | Malformed JSON | `400` | A |
| 6 | Empty `input[]` | `422`, runner never called | A |
| 7 | Unknown field | `422` | A |
| 8 | `Accept: text/event-stream` | SSE | A |
| 9 | `Accept` absent or `*/*` | JSON | A |
| 10 | `Accept: application/xml` | `406` | A |
| 11 | `Content-Type: text/plain` | `415` | A |
| 12 | Body over cap | `413` | A |
| 13 | Cap reached | `429` + `Retry-After` | A |
| 14 | Not ready | `503` | A |
| 15 | Crash mid-stream | sent events stand; `turn.failed`; HTTP stayed `200` | A |
| 16 | Failure before streaming, JSON | `200` + `unavailable` — **not** `502` | A |
| 17 | `[lon, lat]` | `[72.93, 22.56]` → `Point(lon=72.93, lat=22.56)` | A |
| 18 | `user_context` absent | `user_id == "anonymous"` | A |
| 19 | `trace_id` | from `traceparent`, never the body; in every event | A |
| 20 | `message_id` | echoed; `response_message_id` minted, different | A |
| 21 | HTTP layer → core | importing `core/*/service.py` from `adapters/http/**` rejected | A |
| 22 | core → framework | existing `test_framework_boundary.py` | A |
| 23 | Port conformance | every impl callable through a port-annotated parameter | A |
| 24 | Moderation rejects | zero claims, `rejected`, `unsafe_illegal`, one refusal | B |
| 25 | Moderation rejects | `StubLLM` **never called** — proves the early exit | B |
| 26 | Below the floor | `recognise_intent` returns `None`, never a made-up `Intent` | B |
| 27 | Intent `None` | `no_match`, `intent_low_confidence` | B |
| 28 | Stage order | telemetry saw `moderation`, `intent`, `channel`, in order | B |
| 29 | `TelemetrySink` raises | turn still completes | B |
| 30 | `TelemetrySink` content | no line contains the query | B |
| 31 | `TurnSink` content | record **does** carry query, answer, refusals | B |
| 32 | `TurnSink` raises | turn **fails** — a lost audit record is not a success | B |

Cases 25, 28, 29–32 only a wired-up system can answer. They are statements about
assembly, not about any one function.

**Case 21 does not exist yet.** `test_framework_boundary.py` checks that `core/`
imports no framework; nothing checks that hop 1 may not skip hops 2 and 3. Same
AST walk, new rule: `adapters/http/**` and `entrypoint/**` may import `ports/**`
and `core/shared/models`, nothing else from `core/`.

---

## Build order

Each step ends green.

**A** — 1. `core/shared/models.py` + `builders.py` *(settle `ref` first, below)* ·
2. `ports/turn.py` · 3. `fakes.py` · 4. `mapping.py` + `sse.py`, tests first —
**pure, writable today, no framework needed** · 5. `schema.py`, driven by 4's
failures · 6. ADR-0003, then `router.py`, `problem.py`, `entrypoint/` ·
7. `stub_runner.py` + `composition.py`, plus the e2e smoke · 8. boundary and
conformance checks, `CLAUDE.md` tier rows, coverage gate.

**B** — 9. `ports/llm.py`, `ports/sinks.py` · 10. the three core packages ·
11. `adapters/llm/stub.py`, `adapters/sinks/` · 12. `core_runner.py` replaces
`StubRunner`.

Steps 1–5 are unblocked right now. Step 6 is the only thing ADR-0003 holds up.

---

## Needs a decision

| Thing | Who | Blocks |
|---|---|---|
| **Does `ref` exist?** design-v2 §3 has `SubjectRef.ref: SecretStr`; the proposal dropped it. If it stays, `UserTurn` gains a secret field and on-behalf-of Provider calls are in scope | Bhavesh | step 1 |
| **`Outcome` values.** design-v2 has 4; the proposal has 6 (adds `unavailable`, `partially_answered`) | Bhavesh | step 1 |
| **`Location` shape.** design-v2 has `district · state · lat · lon`; the proposal has ISO 3166-2 + GeoJSON | Bhavesh | step 1 |
| **Web framework** — FastAPI / Starlette / Litestar. ADR-0003 | Kelvin | step 6 |
| **Type checker.** `isinstance` on a Protocol checks names, not signatures, and the repo has none — nothing catches `run(turn, context)` against `run(turn, ctx)` | Kelvin | case 23 |
| **Coverage gate.** Asked for in the session; `pyproject.toml` has no `--cov`, no `pytest-cov`, CI has no coverage step | Kelvin | step 8 |
| **ADR for the agent / plain-code split.** It decides which core function takes an LLM port; `DSS_ARCHITECTURE.md` §3 doesn't record it | Bhavesh / Kelvin | step 10 |

Not blocking: where the real LLM adapter lives (only `orchestration/` may import
`pydantic_ai`, so `adapters/llm/` must use a vendor SDK); whether `core/channel`
splits into composer and channel shaper; the cap and heartbeat numbers.

---

## Contract rules to code against

**One endpoint.** `POST /v1/turns`. `Accept` picks JSON or SSE — no streaming flag.

**Outcome is one field.**

```
status : answered | partially_answered | rejected | no_match | requires_input | unavailable
cause  : string | null
```

`unavailable` is the failure value, so there is no second `kind` field. Path is
`message.outcome`.

**Status codes.** `200` · `400` malformed · `406` bad `Accept` · `413` too big ·
`415` wrong content type · `422` invalid · `429` cap · `503` not ready. No
`401`/`403` (no auth), no `502`/`504`.

Once the first byte is out the code cannot change, so a dependency failure
**always** lands in `outcome`, both modes.

**Events.** `turn.created` (0) · `claim.completed` (1..n) · `turn.completed` or
`turn.failed` (n+1). `turn.failed` only when `status: unavailable`. The terminal
event is authoritative — never infer the result from the connection closing. No
`id:`; the stream is not resumable. Every SSE turn gets `turn.created` and a
terminal event whatever the outcome; `claim.completed` only when there is content.

**Fields.** `snake_case` in JSON (`CONVENTIONS.md:17`) · unknown fields rejected ·
citations are `source_ids` per block, **no character offsets** (undefined unit,
breaks in Devanagari) · `trace_id` from `traceparent`, in every response body ·
`transaction_id` echoed, never interpreted · coordinates flat `[lon, lat]` ·
input content `text | image`, output `text | refusal` · no `confidence` in v1.

---

## Verify

```bash
uv sync
uv run ruff check . && uv run ruff format --check .

uv run pytest tests/unit                       # 17-23 (A) · 26 (B)
uv run pytest tests/integration/entrypoint     # 5-16  (A)
uv run pytest tests/conformance/v1             # 3, 4  (A)
uv run pytest tests/integration/orchestration  # 24-32 (B)
uv run pytest                                  # everything; tier 6 excluded
```

Then the point of it all:

```bash
uv run <asgi-server> dss.entrypoint.app:app &
curl -N -X POST localhost:8000/v1/turns \
  -H 'Content-Type: application/json' -H 'Accept: text/event-stream' \
  -d @docs/api-contracts/examples/answered_streaming.json
```

A valid contract-shaped answer from a runner that read none of the request. Every
seam proven. Everything after this is a body swap.
