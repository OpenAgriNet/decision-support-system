# 0003 —  `POST /v1/turns` end to end with a MOCKED core

**Issue:** #3 · **Status:** in review · **Depends on:** [`0002-entrypoint-http-adapter.md`](./0002-entrypoint-http-adapter.md), ADR-0003 (web framework, not yet written)



## Scope

**In:** the five hops wired end to end; skeleton bodies for three core functions;
one read driven-port and one write driven-port with fake adapters; the test
doubles; the case suite that must pass.

**Out, deliberately:** real moderation, real intent recognition, real routing,
planning, execution, review; Pydantic AI; a real model call; enrichment, persona,
routing, execution, review packages (`CLAUDE.md`: *create a subpackage when its
slice is built, not ahead of it*).

---

## 1. Reasoning — why a skeleton before logic

| If you build logic first | If you build the skeleton first |
|---|---|
| The port is designed against the one adapter you have, so it mirrors that SDK | The port is designed against a fake and a real adapter at once — mirroring is visible immediately |
| Streaming is retrofitted after the response shape is fixed | Claim-at-a-time is the shape from hop one; a batch response never becomes the default |
| "Does moderation reject before the LLM is called?" is answered by reading code | It is answered by a test, on commit one |
| An anaemic core looks fine — nothing is in it yet either way | An anaemic core is a *finding*: if replacing a skeleton body needs a seam change, the seam was wrong |
| First runnable turn arrives after the last service | First runnable turn arrives now, and `curl` output is reviewable against the contract |

The skeleton is also the only cheap moment to discover that a seam is in the
wrong place — before eight services depend on it.

**One rule for every skeleton body:** the *signature* is final, the *body* is
disposable. If a real implementation later needs a different signature, that is a
design finding, not a refactor.

---

## 2. The flow

```
  ┌─────────────────────┐
  │ 1. PRIMARY ADAPTER  │  adapters/http/v1/ + entrypoint/
  │    POST /v1/turns   │  wire bytes -> domain objects
  └──────────┬──────────┘
             │ UserTurn, TurnContext
             ▼
  ┌─────────────────────┐
  │ 2. IN-PORT          │  ports/turn.py :: TurnRunner
  │    TurnRunner       │  the only thing the transport may call
  └──────────┬──────────┘
             │
             ▼
  ┌─────────────────────┐
  │ 3. ORCHESTRATOR     │  orchestration/runner.py
  │    sequences core   │  plain async generator — ADR-0001 §4, no graph yet
  └──────────┬──────────┘
             │ calls, in order
             ▼
      ┌──────────────────────────────────┐
      │  core/moderation  screen()       │  ← skeleton bodies
      │  core/intent      recognise()    │
      │  core/channel     compose()      │
      └──────────┬───────────────────────┘
                 │ needs
                 ▼
  ┌─────────────────────┐
  │ 4. OUT-PORTS        │  ports/llm.py :: LLMProvider        (read)
  │    driven ports     │  ports/evidence.py :: EvidenceSink  (write)
  └──────────┬──────────┘
             │ implemented by
             ▼
  ┌─────────────────────┐
  │ 5. SECONDARY ADAPTER│  adapters/llm/scripted_provider.py
  │                     │  adapters/evidence/stdout_sink.py
  └─────────────────────┘

  Return path: AsyncIterator[TurnEvent] back up hops 3 -> 2 -> 1,
               framed as SSE or drained to JSON.
```

Two arrows worth naming, because they run opposite directions and that inversion
*is* the pattern:

- **Runtime:** transport → port → orchestrator → core → port → adapter → outside.
- **Imports:** adapter → port ← core. The port imports nothing.

---

## 3. Planned file structure

New in this change marked `+`. Nothing else is touched.

```
src/dss/
├── ports/
│   ├── turn.py                          +  TurnRunner            (in-port,  hop 2)
│   ├── llm.py                           +  LLMProvider           (out-port, hop 4, read)
│   └── evidence.py                      +  EvidenceSink          (out-port, hop 4, write)
│
├── core/
│   ├── shared/
│   │   └── models.py                    +  domain language (0002 §4)
│   ├── moderation/
│   │   ├── models.py                    +  Screening, ScreeningOutcome
│   │   └── service.py                   +  screen()          [SKELETON body]
│   ├── intent/
│   │   ├── models.py                    +  Intent, ActionType
│   │   └── service.py                   +  recognise_intent() [SKELETON body]
│   └── channel/
│       ├── models.py                    +  ComposedAnswer
│       └── service.py                   +  compose()         [SKELETON body]
│
├── orchestration/
│   └── runner.py                        +  TurnRunner impl — sequences the three, yields events
│
├── adapters/
│   ├── http/v1/                         +  router · schema · mapping · sse · problem   (0002 §5-§8)
│   ├── llm/
│   │   └── scripted_provider.py         +  canned structured output, no network
│   └── evidence/
│       └── stdout_sink.py               +  one JSON line per stage, no PII
│
└── entrypoint/
    ├── app.py                           +  ASGI app, lifespan, readiness
    ├── composition.py                   +  the only file naming concrete types
    └── settings.py                      +  concurrency cap, body cap, release id

tests/
├── support/
│   ├── fakes.py                         +  FakeTurnRunner, FailingTurnRunner, ScriptedLLM, RecordingSink
│   └── builders.py                       +  a_turn(), a_request_body() — one place fixtures are shaped
├── unit/
│   ├── adapters/http/v1/
│   │   ├── test_mapping.py              +  both directions, pure
│   │   └── test_sse.py                  +  frame bytes, sequence numbers
│   └── core/
│       ├── moderation/test_service.py   +
│       ├── intent/test_service.py       +
│       └── channel/test_service.py      +
├── integration/
│   ├── entrypoint/test_turn_route.py    +  ASGI client + FakeTurnRunner
│   └── orchestration/test_runner.py     +  real runner, real skeleton core, fake ports
├── conformance/v1/
│   └── test_worked_examples.py          +  every §4 example, byte-for-byte
└── e2e/
    └── test_turn_smoke.py               +  app -> answer, everything fake but real wiring
```

**Why `core/channel` and not a fourth stage.** Three functions are the minimum
that exercise all five hops with distinct responsibilities: `moderation` proves
early exit without any port, `intent` proves a *read* driven port, `channel`
proves the multi-claim streaming shape. Adding `enrichment`, `routing`,
`persona`, `execution`, or `review` now would create empty packages for
undesigned work — `CLAUDE.md` forbids it, and 0001 already rejected it once.

---

## 4. Hop 1 — primary adapter

Fully specified in 0002 §5–§8. Skeleton adds only `entrypoint/`:

```python
# entrypoint/composition.py — the one function allowed to name every concrete type
def build_runner(settings: Settings) -> TurnRunner:
    return SkeletonTurnRunner(
        llm=ScriptedLLMProvider(),      # swap for a real provider: one line, here
        evidence=StdoutEvidenceSink(),
    )
```

```python
# entrypoint/app.py
def build_app(settings: Settings) -> ASGIApp:
    runner = build_runner(settings)                  # once, at lifespan start
    return mount(v1_router(runner=runner, settings=settings))
```

`app.py` is the only file that both constructs and mounts. `router.py` receives a
`TurnRunner` and never builds one — that is what keeps hop 1 testable against a
fake.

---

## 5. Hop 2 — the in-port

```python
# src/dss/ports/turn.py
class TurnRunner(Protocol):
    def run(self, turn: UserTurn, ctx: TurnContext) -> AsyncIterator[TurnEvent]: ...
```

One method. The response mode (SSE vs JSON) never crosses it — that is hop 1's
business. The port's whole job is to make hop 1 testable without hop 3, and hop 3
replaceable without hop 1.

**Port-correctness check** (`HEXAGONAL_ARCHITECTURE.md` §3.3): could a different
implementation satisfy this without contortion? Yes — `FakeTurnRunner` and
`SkeletonTurnRunner` both do, today, and a Pydantic-AI graph runner will later.
Three implementations on day one is the evidence the port inverted something.

---

## 6. Hop 3 — the orchestrator, and where the dummy logic lives

### 6.1 The dummy logic goes in `core/`, not in `orchestration/`

Tempting alternative: put stub stage functions in `orchestration/stages/` and
leave `core/` empty until real logic exists. Rejected — when the real services
land, every orchestration node is rewritten, and the seam is never exercised.

Instead: `core/*/service.py` gets its **real signature** and a **disposable
body**. `HEXAGONAL_ARCHITECTURE.md` §3.2 — *the public function signature of
`core/*/service.py` is the driving port* — so writing the signature now is
designing the port, and the body is the only throwaway.

Marker convention, so a skeleton cannot be forgotten:

```python
# SKELETON(#82): replaced by the real moderation policy evaluator
```

### 6.2 The orchestrator is a plain async generator

ADR-0001 §4: sequential composition in ordinary Python; `pydantic-graph` is the
declared escalation, not the starting point. So no graph, no `Agent`, and — for
now — no `pydantic_ai` import anywhere.

```python
# src/dss/orchestration/runner.py
class SkeletonTurnRunner:                       # structurally satisfies TurnRunner
    def __init__(self, *, llm: LLMProvider, evidence: EvidenceSink) -> None:
        self._llm, self._evidence = llm, evidence

    async def run(self, turn: UserTurn, ctx: TurnContext) -> AsyncIterator[TurnEvent]:
        yield TurnStarted()

        screening = screen(turn)                                    # core, no port
        self._evidence.stage("moderation", ctx, screening.outcome.value)
        if screening.outcome is ScreeningOutcome.REJECT:
            yield TurnFinished(
                outcome=TurnOutcome(status=TurnStatus.REJECTED, cause=screening.cause),
                content=(RefusalBlock(text=screening.message),),
            )
            return                                                  # no claims, no LLM

        intent = await recognise_intent(turn, llm=self._llm)         # core -> out-port
        self._evidence.stage("intent", ctx, intent.primary_domain if intent else "none")
        if intent is None:
            yield TurnFinished(
                outcome=TurnOutcome(status=TurnStatus.NO_MATCH, cause=Cause.INTENT_LOW_CONFIDENCE),
                content=(TextBlock(text=NO_MATCH_TEXT),),
            )
            return

        answer = compose(turn, intent)                              # core, no port
        for block in answer.content:
            yield Claim(content=block)                              # streams as produced
        self._evidence.stage("channel", ctx, str(len(answer.content)))

        yield TurnFinished(
            outcome=TurnOutcome(status=TurnStatus.ANSWERED),
            content=answer.content,
            sources=answer.sources,
        )
```

### 6.3 Single Responsibility, stated as one line each

| File | Its one job | Its one reason to change |
|---|---|---|
| `orchestration/runner.py` | **order** — which stage runs when, and which terminal each early exit takes | the sequence changes, or a stage is added |
| `core/moderation/service.py` | is this turn allowed | the policy changes |
| `core/intent/service.py` | what is being asked | the confidence rule or prompt changes |
| `core/channel/service.py` | how the answer is written for the channel | the writing rules change |
| `adapters/llm/*` | vendor call ↔ domain type | the vendor changes |
| `adapters/evidence/*` | domain event → sink line | the sink changes |
| `adapters/http/v1/mapping.py` | wire ↔ domain | the envelope changes |
| `adapters/http/v1/sse.py` | domain event → frame | event names or sequencing change |
| `entrypoint/composition.py` | which concrete types | an adapter is swapped |

The test of that table: `runner.py` contains **no `if` with business meaning**.
Its two branches read a value another module decided (`ScreeningOutcome.REJECT`,
`intent is None`) — control flow, not policy. The moment it grows
`if turn.channel == "voice" and len(text) > 200`, a rule escaped `core/`.

### 6.4 Dummy value flow — one traced turn

Query: *"What is the mandi price of wheat in my district this week?"*

| Hop | File | Called with | Skeleton returns | Replaced by |
|---|---|---|---|---|
| 1 | `mapping.to_user_turn` | `TurnRequest` | `UserTurn(messages=…, channel=WEB, user_id="usr_9921")` | — (real) |
| 3 | `moderation.screen` | `UserTurn` | `Screening(PROCEED)` — `REJECT(unsafe_illegal)` if the query contains a word from `_SKELETON_DENY` | #82 policy evaluator |
| 4 | `llm.complete_structured` | `prompt`, `Intent` | `Intent(primary_domain="mandi-prices", action_type="lookup", confidence=0.9)` — from `ScriptedLLMProvider`'s table | a real provider |
| 3 | `intent.recognise_intent` | `UserTurn`, `llm` | that `Intent`, or `None` under `CONFIDENCE_FLOOR = 0.6` | #83 real prompt |
| 3 | `channel.compose` | `UserTurn`, `Intent` | two `TextBlock`s + one `Source(id="src_1", name="Agmarknet", kind=PROVIDER)` | #84 composer + reviewer |
| 3 | `runner.run` | — | `TurnStarted`, `Claim`, `Claim`, `TurnFinished(ANSWERED)` | — (real) |
| 1 | `sse.frame` | each event | `turn.created` 0 · `claim.completed` 1 · `claim.completed` 2 · `turn.completed` 3 | — (real) |

`_SKELETON_DENY` is deliberately crude — a word list, not a model — because the
reject path must be **deterministic** on commit one. It is the single most
obviously fake thing in the change, which is the point.

`CONFIDENCE_FLOOR = 0.6` is a **real** business rule and stays when the skeleton
body goes. Its test is tier 1 and never changes.

---

## 7. Hop 4 — the out-ports

```python
# src/dss/ports/llm.py                             read port
class LLMProvider(Protocol):
    async def complete_structured(
        self, *, prompt: str, output_type: type[T], max_tokens: int | None = None
    ) -> T: ...

# src/dss/ports/evidence.py                        write port
class EvidenceSink(Protocol):
    def stage(self, name: str, ctx: TurnContext, outcome: str) -> None: ...
```

Two shapes on purpose. The read port returns a domain type, so the *mapping*
direction gets exercised. The write port returns `None`, so the "fire and forget,
never let a sink failure fail a turn" rule gets a home.

Note what `LLMProvider` does **not** carry: no `temperature`, no
`model="gpt-4o"`, no `messages=[…]`, no retry config, no API key. Those are a
vendor's vocabulary; they belong in the adapter's constructor. A port that
carries them has inverted nothing.

`EvidenceSink.stage` takes `outcome: str` and never the query — `DSS_ARCHITECTURE.md`
§6.3 requires non-personal stage evidence. Farmer content reaching this port at
all would be the bug.

---

## 8. Hop 5 — the secondary adapters

```python
# adapters/llm/scripted_provider.py — no network, no vendor SDK, no clock
class ScriptedLLMProvider:
    """Returns a canned instance of the requested output_type. SKELETON(#83)."""
    def __init__(self, table: Mapping[type, object] | None = None) -> None: ...
    async def complete_structured(self, *, prompt, output_type, max_tokens=None):
        return self._table[output_type]          # KeyError is a wiring bug, not a runtime path
```

```python
# adapters/evidence/stdout_sink.py
class StdoutEvidenceSink:
    """One JSON line per stage, keyed by trace_id. Default per dss-design-v2 §3."""
    def stage(self, name, ctx, outcome) -> None:
        print(json.dumps({"trace_id": ctx.trace_id, "stage": name, "outcome": outcome}))
```

Both hold **no business rules**. `ScriptedLLMProvider` decides nothing about
confidence; `StdoutEvidenceSink` decides nothing about what is worth recording.
"Retry twice then fall back to the smaller model" would be policy and belongs in
`core/` or config — not here.

**Real-adapter tension, open.** `HEXAGONAL_ARCHITECTURE.md` §5.2 exempts only
`src/dss/orchestration/**` from the `pydantic_ai` ban, so `adapters/llm/` may not
import it. Either the real provider uses a vendor SDK directly (`openai`,
`httpx`) and Pydantic AI is used only for agent composition in
`orchestration/`, **or** Pydantic AI *is* the provider and the LLM adapter has to
live in `orchestration/` — which breaks the adapter families. Not decided; the
skeleton does not force it. Worth an ADR before the first real provider lands.

---

## 9. Methodology — how each layer is mocked

Three distinct doubles, three distinct reasons. All hand-written, all in
`tests/support/fakes.py`.

| Double | Stands in for | Used by | Why not a `MagicMock` |
|---|---|---|---|
| `FakeTurnRunner(events=[…])` | hop 3 | hop 1 tests | a `MagicMock` answers a method you renamed in the Protocol; a stub class fails, which is the point of having a contract |
| `FailingTurnRunner(after=n)` | hop 3, mid-stream fault | hop 1 tests | needs to yield `n` events then raise — a scripted class states that plainly |
| `ScriptedLLM(table)` | hop 5, read | core + orchestration tests | asserts the *port's* shape is satisfiable by something that is not an SDK |
| `RecordingSink()` | hop 5, write | orchestration tests | lets a test assert *which stages ran, in what order* — the cheapest proof that moderation precedes the LLM call |

```python
class FakeTurnRunner:                          # structurally satisfies TurnRunner
    def __init__(self, events: Sequence[TurnEvent]) -> None: self._events = events
    async def run(self, turn, ctx):
        for e in self._events:
            yield e

class FailingTurnRunner:
    def __init__(self, events, *, after: int) -> None: ...
    async def run(self, turn, ctx):
        for i, e in enumerate(self._events):
            if i == self._after: raise ProviderUnavailable("skeleton")
            yield e
```

### 9.1 Which double at which tier

```
tier 1  core/*/service.py          <- ScriptedLLM                       pure, no server
tier 1  mapping.py · sse.py        <- nothing (pure functions)          no doubles at all
tier 3  orchestration/runner.py    <- ScriptedLLM + RecordingSink       real runner, real core
new     adapters/http + entrypoint <- FakeTurnRunner / FailingTurnRunner ASGI client, no core
tier 7  the app                    <- composition wired to the fakes    real everything else
```

The rule that keeps this honest: **no test uses two tiers' doubles at once.** A
test that needs `FakeTurnRunner` *and* `ScriptedLLM` is testing two things and
gets split.

### 9.2 Protocol conformance is not currently checked

`isinstance` against a `Protocol` verifies method *names*, not signatures, and
the repo has no type checker — `pyproject.toml` configures `ruff` and `pytest`
only. So nothing today would catch `SkeletonTurnRunner.run` taking
`(turn, context)` while the port says `(turn, ctx)`.

Two ways to close it; both belong in this change:

1. A `tests/unit/test_port_conformance.py` that calls each implementation
   *through* a port-annotated parameter, so a mismatch is a runtime failure.
2. Adopt a type checker (`ty`, `mypy`, or `pyright`) in `pre-commit` and CI.
   Structural typing is load-bearing in this design and is currently unverified.

Option 2 is a tooling decision with trade-offs → **ADR owed.**

---

## 10. Cases the skeleton must satisfy

Acceptance is this table green, not "it runs".

| # | Case | Tier | Expected |
|---|---|---|---|
| 1 | Answered, streaming | new | `turn.created` 0 · `claim.completed` 1 · `claim.completed` 2 · `turn.completed` 3; `status: answered`; `sources` present |
| 2 | Answered, non-streaming | new | `200`, terminal body only, no event wrapper, `sequence_number` absent |
| 3 | The same turn both ways | conformance | identical `message` object in case 1's terminal event and case 2's body |
| 4 | Moderation rejects | tier 3 | **zero** `Claim` events, `status: rejected`, `cause: unsafe_illegal`, one `refusal` block |
| 5 | Moderation rejects | tier 3 | `ScriptedLLM` was **never called** — the cheap proof the early exit is real |
| 6 | Intent below `CONFIDENCE_FLOOR` | tier 1 | `recognise_intent` returns `None`, never a fabricated `Intent` |
| 7 | Intent `None` | tier 3 | `status: no_match`, `cause: intent_low_confidence` |
| 8 | Stage order | tier 3 | `RecordingSink` saw `moderation`, `intent`, `channel` — in that order |
| 9 | Fault mid-stream | new | events already sent stand; terminal `turn.failed`, `status: unavailable`, `cause: internal`; HTTP stayed `200` |
| 10 | Fault before streaming, JSON mode | new | `200` with `status: unavailable` — **not** `502`, per 0002 §1.7 |
| 11 | Malformed JSON body | new | `400` |
| 12 | Empty `input[]` | new | `422`, and `TurnRunner` was never called |
| 13 | Unknown request field | new | `422` (`extra="forbid"`) |
| 14 | `Accept: text/event-stream` | new | SSE |
| 15 | `Accept` absent, or `*/*` | new | JSON |
| 16 | `Accept: application/xml` only | new | `406` |
| 17 | `Content-Type: text/plain` | new | `415` |
| 18 | Body over the decompressed cap | new | `413` |
| 19 | Concurrency cap reached | new | `429` + `Retry-After` |
| 20 | Not ready | new | `503` |
| 21 | `[lon, lat]` order | tier 1 | `coordinates: [72.93, 22.56]` → `Point(lon=72.93, lat=22.56)`; the reversed assertion fails |
| 22 | `user_context` absent | tier 1 | `user_id == "anonymous"` |
| 23 | `trace_id` | tier 1 | taken from `traceparent`, never from the body; present in every event body |
| 24 | `message_id` | tier 1 | echoed; `response_message_id` minted and different |
| 25 | Devanagari text | conformance | §4's answered example byte-for-byte, `sources` intact |
| 26 | Sink raises | tier 3 | the turn still completes — an evidence failure never fails a turn |
| 27 | Sink content | tier 3 | no line contains the query text (`DSS_ARCHITECTURE.md` §6.3) |
| 28 | Boundary: transport → core | tier 1 | importing `core/*/service.py` from `adapters/http/**` is rejected |
| 29 | Boundary: `core/` → framework | tier 1 | existing `test_framework_boundary.py`, unchanged |
| 30 | Port conformance | tier 1 | every implementation is callable through its port-annotated parameter (§9.2) |

Cases 5, 8, 26, 27 are the ones that only a skeleton can give you. They are
statements about *assembly*, and no amount of unit testing on real services would
have asked them.

**Case 28 is a new check.** `tests/unit/test_framework_boundary.py` walks `core/`
for framework imports; nothing enforces the direction that matters most here —
that hop 1 may not skip hops 2 and 3. Same AST walk, new rule set:
`adapters/http/**` and `entrypoint/**` may import `ports/**` and
`core/shared/models`, and nothing else from `core/`.

---

## 11. Build order

Each step ends green. No step needs the next one to exist.

1. `core/shared/models.py` + `tests/support/builders.py` — the domain language.
2. `ports/turn.py`, `ports/llm.py`, `ports/evidence.py` — signatures only.
3. `tests/support/fakes.py` — all four doubles.
4. `adapters/http/v1/mapping.py` + `sse.py`, tier 1 first (cases 21–24, and 1's
   framing). **Pure — no web framework needed, so this is writable before ADR-0003.**
5. `adapters/http/v1/schema.py` — driven by 4's failures.
6. ADR-0003, then `router.py`, `problem.py`, `entrypoint/` — cases 11–20 against
   `FakeTurnRunner`.
7. `core/moderation`, `core/intent`, `core/channel` skeleton bodies — cases 6, 21.
8. `adapters/llm/scripted_provider.py`, `adapters/evidence/stdout_sink.py`.
9. `orchestration/runner.py` — cases 4, 5, 7, 8, 26, 27.
10. `entrypoint/composition.py` wires it; cases 1, 2, 9, 25 and the e2e smoke.
11. Boundary checks (28, 30) and the `CLAUDE.md` tier-table rows.

Steps 1–5 are unblocked today. Step 6 is the only ADR-0003 dependency.

---

## 12. Skeleton removal

The change that lands each real service deletes exactly one marker and must not
touch a seam. If it does, record why — that is the design finding this whole plan
exists to surface early.

| Marker | Replaced by | Signature changes? |
|---|---|---|
| `SKELETON(#82)` in `core/moderation/service.py` | policy evaluator over the Policies primitive | no |
| `SKELETON(#83)` in `core/intent/service.py` + `adapters/llm/scripted_provider.py` | real prompt + a real `LLMProvider` | no |
| `SKELETON(#84)` in `core/channel/service.py` | Response Composer + Reviewer | no — but gains `enrichment`/`routing`/`execution` upstream of it, so `runner.py` grows stages |

A grep for `SKELETON(` in CI with an issue number required per marker keeps a
disposable body from quietly becoming the implementation.

---

## 13. Open items

- **ADR-0003, web framework.** Blocks step 6 only.
- **Type checker.** §9.2 — structural typing is load-bearing and unverified.
  Needs an ADR.
- **Where the real `LLMProvider` lives.** §8's `pydantic_ai` exemption tension.
  Needs an ADR before the first real provider.
- **`core/channel` naming.** It currently does composition *and* channel shaping.
  `DSS_ARCHITECTURE.md` §3 lists them as separate logical functions; the skeleton
  merges them because neither exists yet. Split when the Reviewer lands.
- **`Screening.message`** — the refusal text is written by moderation in the
  skeleton. Real refusals need `target_lang` and channel shaping, which is
  `core/channel`'s job. The seam between "why refused" and "what the farmer
  reads" is not designed.
- **Concurrency cap number**, body cap number, SSE heartbeat interval — all
  configured, none chosen.
- Everything still open in 0002 §13.

---

## 14. Verification

```bash
uv sync
uv run ruff check . && uv run ruff format --check .

uv run pytest tests/unit                      # cases 6, 21-24, 28-30
uv run pytest tests/integration/orchestration  # cases 4, 5, 7, 8, 26, 27
uv run pytest tests/integration/entrypoint     # cases 9-20
uv run pytest tests/conformance/v1             # cases 3, 25
uv run pytest                                  # all of it; tier 6 excluded
```

Then the thing a skeleton exists for — a real turn over the wire:

```bash
uv run <asgi-server> dss.entrypoint.app:app &
curl -N -X POST localhost:8000/v1/turns \
  -H 'Content-Type: application/json' -H 'Accept: text/event-stream' \
  -d @docs/api-contracts/examples/answered_streaming.json
```

Expected output is §4's answered-streaming example, byte-for-byte, produced by a
core that knows nothing. Every seam is then proven, and every remaining change is
a body swap.
