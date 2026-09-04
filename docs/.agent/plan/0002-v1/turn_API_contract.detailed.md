# 0003 — `POST /v1/turns` end to end with a mocked core

**Issue:** #3 · **Status:** in review · **Depends on:** ADR-0003 (web framework, not yet written)
**Aligned against:** the Bhavesh / Kelvin session of 2026-09-03 · **Spec of record:** `docs/dss-design-v2.md`

> The earlier `0002-entrypoint-http-adapter.md` is no longer in the repo. Its
> contract decisions — which this plan builds on — are preserved in
> **Appendix A**, so this document stands alone. References that used to read
> "0002 §n" now point there.


## Scope — two changes, not one

The 2026-09-03 session scoped the immediate deliverable as *"test the
orchestrator **port**, not the internal orchestrator implementation — a thin
slice where the API layer communicates with the port to confirm requests are
received and responses returned,"* with a dummy orchestrator returning dummy
data. That is narrower than the full skeleton, so the work splits:

### 0003a — the agreed thin slice **[do this first]**

**In:** `ports/turn.py`; `core/shared/models.py`; `adapters/http/v1/`
(`router` · `schema` · `mapping` · `sse` · `problem`); `entrypoint/`
(`app` · `composition` · `settings`); a `DummyTurnRunner` that returns canned
`TurnEvent`s and calls no core function at all; the transport test doubles.

**Cases:** 1–3, 9–25, 28–30.

**Out:** every `core/*/service.py`, both out-ports, both secondary adapters. The
dummy runner has no dependencies to inject, so hops 4 and 5 do not exist yet.

### 0003b — the deep skeleton **[follow-up]**

**In:** `core/moderation` · `core/intent` · `core/channel` skeleton bodies;
`ports/llm.py`; `ports/sinks.py`; `adapters/llm/` and `adapters/sinks/` fakes;
`orchestration/runner.py` replacing the dummy.

**Cases:** 4–8, 26–27b.

**Out, deliberately:** real moderation, real intent recognition, routing,
planning, execution, review; Pydantic AI; any real model call; `enrichment`,
`persona`, `routing`, `execution`, `review` packages (`CLAUDE.md`: *create a
subpackage when its slice is built, not ahead of it*).

Sections 4–5 and 9–10 below serve 0003a. Sections 6–8 serve 0003b.

---

## 0. Alignment record — 2026-09-03 session

**Decisions this plan implements:**

| Decision | Where it lands |
|---|---|
| Dummy orchestrator for now | 0003a's `DummyTurnRunner`; §6's real skeleton is 0003b |
| **Streaming handled in the API layer, not the orchestrator** | §5 — the port carries no mode; `sse.py` owns framing and `sequence_number`; `router.py` owns `Accept`. This is also why the port *returns* an iterator rather than taking a push-sink: a push sink would put stream control in orchestration. |
| Test the **port**, not the orchestrator internals | the 0003a / 0003b split above |
| TDD, tier 1 first | §11 build order |
| Intent · moderation · planner · response composer are **agents**; tool · provider · skill discovery are **plain code** | §6.2 signatures — moderation and channel take `llm`; discovery functions, when built, take none |
| Custom API format; open responses rejected | ADR-0002; the evaluation stays at `docs/api-contracts/open-responses-api.md` |

**Blocking conflicts — `dss-design-v2.md` is the spec of record and disagrees
with the single-endpoint proposal in three places. Owner: Bhavesh, in the
contract update. None of them is this plan's to settle:**

| `dss-design-v2.md` §3 | The proposal / Appendix A |
|---|---|
| `SubjectRef.ref: SecretStr` — "the DSS never opens it" | `ref` dropped; **no Provider call on a farmer's behalf in v1** (A.2) |
| `Outcome` = `PROCEED · REJECT · CLARIFY · NO_MATCH` (4) | six statuses, adding `unavailable` and `partially_answered` |
| `Location` = `district · state · lat · lon` (2 dp) | ISO 3166-2 `region` + free-text `area` + GeoJSON `[lon, lat]` |

If `ref` survives, A.2 reverses and `UserTurn` regains a secret field — a
`core/shared/models.py` change, so it is worth settling before step 1.

**Owed elsewhere:**

- **An ADR for the agent/plain-code classification.** It decides which core
  function carries an `LLMProvider` parameter, and therefore which are cheap to
  test. `DSS_ARCHITECTURE.md` §3 does not record it. `CLAUDE.md` requires an ADR
  for a decision with these trade-offs.
- **A coverage gate.** The session called for high coverage "as required by the
  development pipelines". `pyproject.toml` has
  `addopts = ["-ra", "--strict-config", "-m", "not eval"]` — no `--cov`, no
  `pytest-cov` dependency, and no coverage step in `.github/workflows/ci.yml`.
  Nothing enforces it today.
- **The contract rewrite** is Bhavesh's action item, not this change's. This plan
  supplies Appendix A and the conflicts above as its input.

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
│   ├── llm.py                           +  LLMProvider           (out-port, hop 4, read)   0003b
│   └── sinks.py                         +  TurnSink (required) · TelemetrySink (optional)  0003b
│
├── core/
│   ├── shared/
│   │   └── models.py                    +  domain language (0002 §4)
│   ├── moderation/                      0003b — AGENT, takes llm
│   │   ├── models.py                    +  Screening, ScreeningOutcome
│   │   └── service.py                   +  screen(turn, *, llm)              [SKELETON body]
│   ├── intent/                          0003b — AGENT, takes llm
│   │   ├── models.py                    +  Intent, ActionType
│   │   └── service.py                   +  recognise_intent(turn, *, llm)    [SKELETON body]
│   └── channel/                         0003b — AGENT (response composer), takes llm
│       ├── models.py                    +  ComposedAnswer
│       └── service.py                   +  compose(turn, intent, *, llm)     [SKELETON body]
│
├── orchestration/
│   ├── dummy_runner.py                  +  0003a — canned events, calls no core function
│   └── runner.py                        +  0003b — sequences the three, yields events
│
├── adapters/
│   ├── http/v1/                         +  router · schema · mapping · sse · problem   (0002 §5-§8)
│   ├── llm/                             0003b
│   │   └── scripted_provider.py         +  canned structured output, no network
│   └── sinks/                           0003b
│       ├── memory_turn_sink.py          +  the turn record — holds farmer content, on purpose
│       └── stdout_telemetry_sink.py     +  one JSON line per stage, never farmer content
│
└── entrypoint/
    ├── app.py                           +  ASGI app, lifespan, readiness
    ├── composition.py                   +  the only file naming concrete types
    └── settings.py                      +  concurrency cap, body cap, release id

tests/
├── support/
│   ├── fakes.py                         +  FakeTurnRunner, FailingTurnRunner, ScriptedLLM,
│   │                                       RecordingTurnSink, RecordingTelemetrySink
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
│   ├── entrypoint/test_turn_route.py    +  0003a — ASGI client + FakeTurnRunner
│   └── orchestration/test_runner.py     +  0003b — real runner, real skeleton core, fake ports
├── conformance/v1/
│   └── test_worked_examples.py          +  every §4 example, byte-for-byte
└── e2e/
    └── test_turn_smoke.py               +  app -> answer, everything fake but real wiring
```

**Why these three (0003b).** All three are **agents** under the 2026-09-03
classification, so all three take an `LLMProvider` and all three exercise the
read port. What each adds beyond that is distinct: `moderation` proves the early
exit, `intent` proves the `None` path under a real confidence rule, `channel`
proves the multi-claim streaming shape.

The classification also says what stays out: **tool, provider, and skill
discovery are plain code** and take no `llm`. Building one now would prove
nothing the three already prove, and adding `enrichment`, `persona`, `routing`,
`execution`, or `review` packages would create empty directories for undesigned
work — `CLAUDE.md` forbids it, and 0001 rejected it once already.

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

**0003a — the dummy.** No core, no ports, nothing to inject. It exists so the
port can be tested at all:

```python
# src/dss/orchestration/dummy_runner.py — SKELETON(#81)
class DummyTurnRunner:                          # structurally satisfies TurnRunner
    async def run(self, turn: UserTurn, ctx: TurnContext) -> AsyncIterator[TurnEvent]:
        yield TurnStarted()
        for block in _CANNED_CONTENT:
            yield Claim(content=block)
        yield TurnFinished(
            outcome=TurnOutcome(status=TurnStatus.ANSWERED),
            content=_CANNED_CONTENT,
            sources=_CANNED_SOURCES,
        )
```

It reads `turn` for nothing. That is the point: if hop 1 passes its cases against
this, hop 1 is proven independent of every hop below it.

**0003b — the real sequencing.** All three stages are agents, so all three take
the port:

```python
# src/dss/orchestration/runner.py
class SkeletonTurnRunner:                       # structurally satisfies TurnRunner
    def __init__(self, *, llm: LLMProvider, turns: TurnSink, telemetry: TelemetrySink) -> None:
        self._llm, self._turns, self._telemetry = llm, turns, telemetry

    async def run(self, turn: UserTurn, ctx: TurnContext) -> AsyncIterator[TurnEvent]:
        yield TurnStarted()
        self._turns.opened(ctx, turn)                               # required sink, holds content

        screening = await screen(turn, llm=self._llm)               # AGENT -> out-port
        self._telemetry.stage("moderation", ctx, screening.outcome.value)
        if screening.outcome is ScreeningOutcome.REJECT:
            finished = TurnFinished(
                outcome=TurnOutcome(status=TurnStatus.REJECTED, cause=screening.cause),
                content=(RefusalBlock(text=screening.message),),
            )
            self._turns.closed(ctx, finished)
            yield finished
            return                                                  # no claims

        intent = await recognise_intent(turn, llm=self._llm)        # AGENT -> out-port
        self._telemetry.stage("intent", ctx, intent.primary_domain if intent else "none")
        if intent is None:
            finished = TurnFinished(
                outcome=TurnOutcome(status=TurnStatus.NO_MATCH, cause=Cause.INTENT_LOW_CONFIDENCE),
                content=(TextBlock(text=NO_MATCH_TEXT),),
            )
            self._turns.closed(ctx, finished)
            yield finished
            return

        answer = await compose(turn, intent, llm=self._llm)         # AGENT -> out-port
        for block in answer.content:
            yield Claim(content=block)                              # streams as produced
        self._telemetry.stage("channel", ctx, str(len(answer.content)))

        finished = TurnFinished(
            outcome=TurnOutcome(status=TurnStatus.ANSWERED),
            content=answer.content,
            sources=answer.sources,
        )
        self._turns.closed(ctx, finished)
        yield finished
```

Every stage is `await`ed now — an agent call is I/O. `screen` being async is a
signature change forced by the classification, and it is exactly the kind of
change this plan exists to catch *before* eight services depend on the old shape.

### 6.3 Single Responsibility, stated as one line each

| File | Its one job | Its one reason to change |
|---|---|---|
| `orchestration/runner.py` | **order** — which stage runs when, and which terminal each early exit takes | the sequence changes, or a stage is added |
| `core/moderation/service.py` | is this turn allowed (agent) | the policy or its prompt changes |
| `core/intent/service.py` | what is being asked (agent) | the confidence rule or prompt changes |
| `core/channel/service.py` | how the answer is written for the channel (agent) | the writing rules change |
| `adapters/llm/*` | vendor call ↔ domain type | the vendor changes |
| `adapters/sinks/memory_turn_sink.py` | the audit record of a turn | the record's store changes |
| `adapters/sinks/stdout_telemetry_sink.py` | stage outcome → one line | the tracing backend changes |
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
| 3 | `moderation.screen` | `UserTurn`, `llm` | `Screening(PROCEED)` — `REJECT(unsafe_illegal)` if the query contains a word from `_SKELETON_DENY`. The body ignores `llm`; the parameter is real | #82 policy evaluator |
| 4 | `llm.complete_structured` | `prompt`, `Intent` | `Intent(primary_domain="mandi-prices", action_type="lookup", confidence=0.9)` — from `ScriptedLLMProvider`'s table | a real provider |
| 3 | `intent.recognise_intent` | `UserTurn`, `llm` | that `Intent`, or `None` under `CONFIDENCE_FLOOR = 0.6` | #83 real prompt |
| 3 | `channel.compose` | `UserTurn`, `Intent`, `llm` | two `TextBlock`s + one `Source(id="src_1", name="Agmarknet", kind=PROVIDER)`. Body ignores `llm` | #84 composer + reviewer |
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

# src/dss/ports/sinks.py                           write ports — two, per dss-design-v2 §3
class TurnSink(Protocol):
    """The record of the turn. REQUIRED. Holds farmer content, on purpose."""
    def opened(self, ctx: TurnContext, turn: UserTurn) -> None: ...
    def closed(self, ctx: TurnContext, finished: TurnFinished) -> None: ...

class TelemetrySink(Protocol):
    """Spans, timings, token counts. OPTIONAL, defaults to stdout. Never farmer content."""
    def stage(self, name: str, ctx: TurnContext, outcome: str) -> None: ...
```

Two shapes on purpose. The read port returns a domain type, so the *mapping*
direction gets exercised. The write ports return `None`, so the failure rules get
a home — and they are **opposite** rules, which is the reason there are two ports
and not one:

- `TelemetrySink` is optional. A raise is swallowed; the turn completes.
- `TurnSink` is required and is the audit trail. A raise **fails the turn** — a
  lost audit record is not an acceptable success.

`dss-design-v2.md` §3 keeps them separate for exactly this reason, and a single
`EvidenceSink` could not carry both rules or both content postures.

Note what `LLMProvider` does **not** carry: no `temperature`, no
`model="gpt-4o"`, no `messages=[…]`, no retry config, no API key. Those are a
vendor's vocabulary; they belong in the adapter's constructor. A port that
carries them has inverted nothing.

`TelemetrySink.stage` takes `outcome: str` and never the query —
`DSS_ARCHITECTURE.md` §6.3 requires non-personal stage evidence, and
`dss-design-v2.md` §3 notes tracing backends capture prompt text by default.
Farmer content reaching *that* port is the bug. Farmer content reaching
`TurnSink` is the requirement. Keeping the two impossible to confuse is what the
split buys.

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
# adapters/sinks/stdout_telemetry_sink.py — the documented default
class StdoutTelemetrySink:
    """One JSON line per stage, keyed by trace_id. Never the query."""
    def stage(self, name, ctx, outcome) -> None:
        print(json.dumps({"trace_id": ctx.trace_id, "stage": name, "outcome": outcome}))

# adapters/sinks/memory_turn_sink.py — SKELETON(#85)
class InMemoryTurnSink:
    """Keeps the turn record in a dict. Replaced by a durable store; the shape is the point."""
    def opened(self, ctx, turn) -> None: ...
    def closed(self, ctx, finished) -> None: ...
```

All three hold **no business rules**. `ScriptedLLMProvider` decides nothing about
confidence; neither sink decides what is worth recording.
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

Five hand-written doubles, each for a distinct reason, all in
`tests/support/fakes.py`. The first two are all 0003a needs — a dummy
orchestrator has nothing under it to fake.

| Double | Stands in for | Used by | Why not a `MagicMock` |
|---|---|---|---|
| `FakeTurnRunner(events=[…])` | hop 3 | hop 1 tests | a `MagicMock` answers a method you renamed in the Protocol; a stub class fails, which is the point of having a contract |
| `FailingTurnRunner(after=n)` | hop 3, mid-stream fault | hop 1 tests | needs to yield `n` events then raise — a scripted class states that plainly |
| `ScriptedLLM(table)` | hop 5, read | core + orchestration tests | asserts the *port's* shape is satisfiable by something that is not an SDK |
| `RecordingTelemetrySink()` | hop 5, write, optional | orchestration tests | lets a test assert *which stages ran, in what order* — the cheapest proof that moderation precedes composition |
| `RecordingTurnSink()` | hop 5, write, required | orchestration tests | asserts the audit record actually carries the query, the answer, and every refusal |

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
tier 3  orchestration/runner.py    <- ScriptedLLM + both RecordingSinks  real runner, real core
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

Cases 1–3 and 9–30 are **0003a**. Cases 4–8 and 26–27b are **0003b**.

| # | Case | Tier | Expected |
|---|---|---|---|
| 1 | Answered, streaming | new | `turn.created` 0 · `claim.completed` 1 · `claim.completed` 2 · `turn.completed` 3; `status: answered`; `sources` present |
| 2 | Answered, non-streaming | new | `200`, terminal body only, no event wrapper, `sequence_number` absent |
| 3 | The same turn both ways | conformance | identical `message` object in case 1's terminal event and case 2's body |
| 4 | Moderation rejects | tier 3 | **zero** `Claim` events, `status: rejected`, `cause: unsafe_illegal`, one `refusal` block |
| 5 | Moderation rejects | tier 3 | `ScriptedLLM` was **never called** — the cheap proof the early exit is real |
| 6 | Intent below `CONFIDENCE_FLOOR` | tier 1 | `recognise_intent` returns `None`, never a fabricated `Intent` |
| 7 | Intent `None` | tier 3 | `status: no_match`, `cause: intent_low_confidence` |
| 8 | Stage order | tier 3 | `RecordingTelemetrySink` saw `moderation`, `intent`, `channel` — in that order |
| 9 | Fault mid-stream | new | events already sent stand; terminal `turn.failed`, `status: unavailable`, `cause: internal`; HTTP stayed `200` |
| 10 | Fault before streaming, JSON mode | new | `200` with `status: unavailable` — **not** `502`, per A.4 |
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
| 26 | `TelemetrySink` raises | tier 3 | the turn still completes — an optional sink never fails a turn |
| 27 | `TelemetrySink` content | tier 3 | no line contains the query text (`DSS_ARCHITECTURE.md` §6.3) |
| 27a | `TurnSink` content | tier 3 | the record **does** carry the query, the answer, and every refusal — that is its job |
| 27b | `TurnSink` raises | tier 3 | the turn **fails** — the audit trail is required, so a lost record is not a success |
| 28 | Boundary: transport → core | tier 1 | importing `core/*/service.py` from `adapters/http/**` is rejected |
| 29 | Boundary: `core/` → framework | tier 1 | existing `test_framework_boundary.py`, unchanged |
| 30 | Port conformance | tier 1 | every implementation is callable through its port-annotated parameter (§9.2) |

Cases 5, 8, 26, 27, 27a, 27b are the ones only a skeleton can give you. They are
statements about *assembly*, and no amount of unit testing on real services would
have asked them. All six are 0003b — which is why 0003a's green suite is not yet
evidence that the architecture holds, only that the transport does.

**Case 28 is a new check.** `tests/unit/test_framework_boundary.py` walks `core/`
for framework imports; nothing enforces the direction that matters most here —
that hop 1 may not skip hops 2 and 3. Same AST walk, new rule set:
`adapters/http/**` and `entrypoint/**` may import `ports/**` and
`core/shared/models`, and nothing else from `core/`.

---

## 11. Build order

Each step ends green. No step needs the next one to exist.

**0003a**

1. `core/shared/models.py` + `tests/support/builders.py` — the domain language.
   Settle the §0 `ref` conflict first; it changes `UserTurn`.
2. `ports/turn.py` — one signature.
3. `tests/support/fakes.py` — `FakeTurnRunner`, `FailingTurnRunner`.
4. `adapters/http/v1/mapping.py` + `sse.py`, tier 1 first (cases 21–24, and 1's
   framing). **Pure — no web framework needed, so this is writable before ADR-0003.**
5. `adapters/http/v1/schema.py` — driven by 4's failures.
6. ADR-0003, then `router.py`, `problem.py`, `entrypoint/` — cases 11–20 against
   `FakeTurnRunner`.
7. `orchestration/dummy_runner.py` + `entrypoint/composition.py` — cases 1–3, 9,
   10, 25, and the e2e smoke.
8. Boundary checks (28, 30), the port-conformance test, the `CLAUDE.md` tier-table
   rows, and the coverage gate from §0.

Steps 1–5 are unblocked today. Step 6 is the only ADR-0003 dependency.

**0003b**

9. `ports/llm.py`, `ports/sinks.py`; the three remaining doubles.
10. `core/moderation`, `core/intent`, `core/channel` skeleton bodies — case 6.
11. `adapters/llm/scripted_provider.py`, `adapters/sinks/*`.
12. `orchestration/runner.py` replaces the dummy — cases 4, 5, 7, 8, 26–27b.

---

## 12. Skeleton removal

The change that lands each real service deletes exactly one marker and must not
touch a seam. If it does, record why — that is the design finding this whole plan
exists to surface early.

| Marker | Replaced by | Signature changes? |
|---|---|---|
| `SKELETON(#81)` in `orchestration/dummy_runner.py` | `SkeletonTurnRunner` (0003b), then the real orchestrator | no — the file is deleted, the port is not touched |
| `SKELETON(#85)` in `adapters/sinks/memory_turn_sink.py` | a durable turn store | no |
| `SKELETON(#82)` in `core/moderation/service.py` | policy evaluator over the Policies primitive | no |
| `SKELETON(#83)` in `core/intent/service.py` + `adapters/llm/scripted_provider.py` | real prompt + a real `LLMProvider` | no |
| `SKELETON(#84)` in `core/channel/service.py` | Response Composer + Reviewer | no — but gains `enrichment`/`routing`/`execution` upstream of it, so `runner.py` grows stages |

A grep for `SKELETON(` in CI with an issue number required per marker keeps a
disposable body from quietly becoming the implementation.

---



## 1. Verification

```bash
uv sync
uv run ruff check . && uv run ruff format --check .

uv run pytest tests/unit                       # cases 21-24, 28-30   (0003a) · 6 (0003b)
uv run pytest tests/integration/entrypoint     # cases 9-20            (0003a)
uv run pytest tests/conformance/v1             # cases 3, 25           (0003a)
uv run pytest tests/integration/orchestration  # cases 4-8, 26-27b     (0003b)
uv run pytest                                  # all of it; tier 6 excluded
```

Then the thing a skeleton exists for — a real turn over the wire:

```bash
uv run <asgi-server> dss.entrypoint.app:app &
curl -N -X POST localhost:8000/v1/turns \
  -H 'Content-Type: application/json' -H 'Accept: text/event-stream' \
  -d @docs/api-contracts/examples/answered_streaming.json
```

Expected output is the contract's answered-streaming example, byte-for-byte —
produced in 0003a by a runner that reads none of the request, and in 0003b by a
core that decides nothing. Every seam is then proven, and every remaining change
is a body swap.

---

## Appendix A — contract decisions this plan builds on

Carried over from the deleted `0002-entrypoint-http-adapter.md` so this document
stands alone. **[assumed]** marks a decision taken to resolve a contradiction in
the single-endpoint proposal rather than agreed in a session — each is a one-line
change if wrong.

### A.1 Outcome is one axis

The proposal's §3 prose declares `outcome.status` (`completed|failed`) plus
`outcome.kind`; its §3 schema, its §3.0 type list, and all four §4 worked
examples implement a single `status` carrying domain values. One axis wins:

```
outcome.status : answered | partially_answered | rejected | no_match | requires_input | unavailable
outcome.cause  : string | null
```

`unavailable` is the execution-failure value, so no second field is needed. Path
is `message.outcome`, not `message.turn.outcome`.

`docs/.agent/plan/0002-v1/dssturnandhexagon.html` asserts the opposite — that §4
implements two axes and §3's schema is the placeholder. That is inverted: `kind`
appears in the prose only, in zero examples. The file should be deleted or
redrawn.

### A.2 No on-behalf-of token in v1 — **contested, see §0**

`subject_ref.ref` / `issuer` / `expires_at` dropped; identity is one
`user_context` entry of type `identity` carrying `user_id`. Consequence: no
Provider can be invoked on a specific farmer's behalf in v1.

`dss-design-v2.md` §3 still carries `ref: SecretStr`. Unresolved.

### A.3 No authentication

Network perimeter only. The router emits no `401` and no `403`.

### A.4 Status codes

`200` · `400` malformed · `406` unsatisfiable `Accept` · `413` over cap ·
`415` wrong `Content-Type` · `422` schema violation · `429` cap reached
(with `Retry-After`) · `503` not ready. `406`/`413`/`415` are **[assumed]**
additions; `401`/`403`/`502`/`504` are dropped.

Once the first response byte is written the code cannot change — every later
failure is a terminal event. A dependency failure therefore **always** lands in
`outcome`, in both modes.

### A.5 Other resolutions

| Decision | Note |
|---|---|
| Citations attach per claim as `source_ids` | `start_index`/`end_index` removed — no defined character unit, and ADR-0002 §5.7 rejected the Responses API partly over this exact hazard in Indian-language scripts |
| `snake_case` on the wire | **[assumed]** — `CONVENTIONS.md:17`, no transformation at the boundary |
| Claims are pulled, not pushed | `AsyncIterator`, not a `ClaimSink`. Confirmed by the 2026-09-03 streaming decision |
| `envelope_version` + `dss_release` | **[assumed]** — the proposal overloads `context.version` with two meanings and two formats |
| `confidence` dropped from v1 | **[assumed]** — undefined semantics per status; `DSS_ARCHITECTURE.md` §8.3 lists confidence as open |
| `message.error{…}` dropped | duplicated `outcome.cause`; `retry_after_seconds` moves onto `outcome` |
| `attributes.domain`, `execution.allowed_interactions` dropped | undeclared, and §2's own rules forbid caller-supplied policy |
| `turn.created` is emitted, `sequence_number: 0` | the proposal's §3.1 table lists it; its §4 example omits it |
| Input content: `text \| image`; output content: `text \| refusal` | separate closed enums; `refusal` was never declared |
| `coordinates: [72.93, 22.56]` | the proposal double-nests it, which `type: "Point"` forbids |
| `trace_id` is response-only, from `traceparent` | never caller-supplied in the body |
| Mode is orthogonal to outcome | every SSE turn gets `turn.created` and a terminal event; `claim.completed` only when there is content. Drops the old "only an answered turn streams" rule |

### A.6 Still open from the contract

`Polygon` geometry · `envelope_version`/`dss_release` naming · file input and the
attachment service contract · retries and duplicate detection on `message_id` ·
the evidence tiers (metrics vs diagnostic, `geometry` never written to a sink) ·
the amul / bharat / mh migration table, now void because `X-Session-Id` became
`context.session_id` and `X-Trace-Id` became `traceparent` · `confidence` ·
the SSE heartbeat interval.
