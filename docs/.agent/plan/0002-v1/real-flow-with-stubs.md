# Reshape the outer layer to the real turn flow, still with no LLM

**Issue:** #3 · **Status:** draft · **Supersedes:** the runner shape in
[`turn_API_contract.md`](./turn_API_contract.md) step B
**Sources of record:** `docs/api-contracts/openapi.yaml` and
`docs/.agent/plan/10-planner-agent-poc.md`, both on `origin/feat/10-planner-agent-poc`

## Why this exists

Two things landed on `feat/10-planner-agent-poc` that the current build predates:

1. **An adopted contract** — `api-contract.md` rewritten plus an OpenAPI 3.1
   spec (`3e56cb3 docs: adopt single-endpoint api contract and add openapi spec`).
   The wire layer here was built against a private reconciliation instead, and
   disagrees with it in seven places. Those are defects, not preferences.
2. **The planner PoC plan** (`6b1b91e`), which fixes the real turn flow and
   makes one decision the current runner contradicts.

Nothing here needs an LLM or a network. The point is to get the **shape** right
while everything expensive is still a stub, so the real components drop into
seams that already exist.

---

## 1. The real flow

From `10-planner-agent-poc.md` plus ADR-0003:

```
   envelope ──▶ UserTurn
                   │
        ┌──────────┴──────────┐
        ▼                     ▼
   classify_intent      moderate            ← parallel (ADR-0003)
        │                     │
        │ Intent.asks         │
        ▼                     │
   discover_providers         │             ← starts when intent lands,
        │                     │               does NOT wait for the verdict
        │ DiscoveryResult     │
        └──────────┬──────────┘
                   ▼
              plan(discovery, verdict=<awaitable>, skills, identity)
                   │
                   │  ═══ THE BARRIER ═══
                   │  await verdict before ANY tool call
                   │
                   ▼ Evidence
              sufficiency(evidence, intent.asks)      ← plain code, no LLM, ever
                   │
                   ▼
              compose(evidence, identity) ──▶ content items + sources
                   │
                   ▼
              one claim.completed per content item, then the terminal event
```

Three things about this that the current runner gets wrong:

- **Intent and moderation are concurrent**, not moderation-then-intent.
- **Discovery is allowed to cross the moderation barrier** — a discovery query is
  read-only and can be thrown away. Provider *invocation* cannot.
- **The gate is not "moderation runs first".** It is "no tool call happens before
  the verdict resolves". A rejected turn may already have run discovery, and
  that is correct.

### 1.1 The planner is deliberately not behind a port

The PoC plan rejects a `run_with_tools(tools=...)` port at length: tool schemas
come from signature and docstring introspection, `RunContext` is
structurally special-cased, `prepare=` and `ModelRetry` are framework types.
A neutral tool type would either reimplement all of it or re-export the vendor's
types — *"the interface's whole vocabulary would be the vendor's, renamed."*

**So do not invent a `Planner` port.** The seam is a function signature that
lives in `orchestration/`:

```python
async def plan(
    discovery: DiscoveryResult,
    *,
    verdict: Awaitable[Screening],
    skills: tuple[Skill, ...],
    identity: Identity,
    invocation: CapabilityInvocation,
) -> Evidence: ...
```

The stub and the real Pydantic AI agent are two bodies behind that one signature.
Testability is unaffected — `FunctionModel` and `Agent.override` cover the real
one at tier 3.

---

## 2. Contract defects to fix first

Checked against `openapi.yaml` on `feat/10-planner-agent-poc`. Every row is a
place the running server is wrong today.

| # | Adopted spec | Built here | Fix |
|---|---|---|---|
| 1 | **camelCase** — `sessionId`, `transactionId`, `sourceLanguage`, `targetLanguage`, `maxCharacters`, `userContext`, `traceId`, `sequenceNumber`, `messageId` | snake_case throughout | `alias_generator=to_camel` + `populate_by_name` on the wire models only. `mapping.py` and `core/` are untouched — this is exactly the seam that makes it a one-file change |
| 2 | `context.transactionId` **required**, and *"Caller-supplied per-turn trace id. Echoed back as `context.traceId`"* | optional; `trace_id` minted from `traceparent`, and a body trace id is *rejected* | `traceId` ← the request's `transactionId`. `traceparent` starts the server span. Mint only when the caller omits it |
| 3 | `Outcome.confidence` **required**, integer 0–100 | dropped as `[assumed]` | Add it. `TurnOutcome.confidence: int`. The stub composer supplies a fixed number |
| 4 | `Outcome.cause` **required**, nullable | omitted when null (`exclude_none=True`) | Serialize `cause: null` explicitly |
| 5 | `ResponseContext` required `[id, version, timestamp, messageId, sessionId, traceId]` — one `version`, no `responseMessageId` | split into `envelope_version` + `dss_release`, plus a minted `response_message_id` | Collapse to `version` (the DSS release). Echo the caller's `messageId` |
| 6 | `sequenceNumber` **minimum 1** | `turn.created` is `0` | Start the counter at 1 |
| 7 | `ResponseMessage.error` → `TurnError` | dropped as duplicating `cause` | Add it back, populated on `unavailable` |

**Casing conflict to settle.** `CONVENTIONS.md:17` says *"snake_case in Python
and JSON — no transformation at the boundary."* The adopted contract is
camelCase. `feat/79`'s `envelope.py` already resolved this in code with a
docstring — *"the envelope is the only place camelCase and provider-shaped JSON
is allowed"*. `CONVENTIONS.md` needs that exception written down, or it and the
contract stay in contradiction.

---

## 3. Target runner

`orchestration/core_runner.py` becomes:

```python
async def run(self, turn, ctx):
    yield TurnStarted()
    self._turns.opened(ctx, turn)

    async with anyio.create_task_group() as tg:        # ADR-0005, not asyncio
        tg.start_soon(self._classify, turn)            # -> Intent
        tg.start_soon(self._moderate, turn)            # -> Screening

    discovery = await discover_providers(intent.asks, adapter=self._discovery)

    evidence = await plan(                             # awaits the verdict inside
        discovery, verdict=verdict, skills=self._skills,
        identity=self._identity, invocation=self._invocation,
    )

    if screening.outcome is not Outcome.PROCEED:
        yield self._refuse(ctx, screening); return

    gaps = check_sufficiency(evidence, intent.asks)    # real code, no LLM
    answer = await compose(evidence, gaps, identity=self._identity, llm=self._llm)

    for item in answer.content:
        yield Claim(content=item)
    yield self._finish(ctx, answer, evidence)
```

Still no `if` with business meaning: every branch reads a value another module
decided.

**Open in the flow:** where the refusal terminal is emitted relative to `plan`.
Written above, `plan` awaits the verdict and returns empty `Evidence` on a
rejection, and the runner then refuses. The alternative is the runner checking
the verdict before calling `plan` at all — simpler, but then discovery's
permission to cross the barrier has no expression in code. Pick one when writing
the barrier test; the test is what makes the difference visible.

---

## 4. The stub inventory

Every stub is a body behind a signature that does not change when the real one
lands.

| Stub | Lives in | Returns | Real version |
|---|---|---|---|
| `StubLLM` | `adapters/llm/stub.py` | canned `Intent`; canned composer text | a real `LLM` adapter |
| `screen` body | `core/moderation/service.py` | `_DENY` word list | #82 policy evaluator |
| `StubDiscovery` | `adapters/discovery/stub.py` | one Direct answer + one OnDemand `ProviderCapability` | `feat/79`'s `HttpDiscoveryClient` |
| `StubInvocation` | `adapters/invocation/stub.py` | canned `on_select` payload, records every call | `HttpCapabilityInvocation` |
| `plan` body | `orchestration/planner.py` | awaits the verdict, calls `invocation.select` once per capability, assembles `Evidence` — **no agent, no model** | the Pydantic AI tool loop |
| `compose` body | `core/channel/service.py` | fixed sentences citing `Evidence` sources | #84 composer + reviewer |
| `MemoryTurnSink` | `adapters/sinks/memory.py` | dict | #85 durable store |

**`check_sufficiency` is not a stub.** The PoC plan is explicit: it compares
`Evidence.served` against `Intent.asks` indices, *"plain code, no LLM"*. Write it
for real now — it is cheap, it is the kind of rule tier 1 exists for, and
stubbing it would prove nothing.

The stub planner is the important one. It is not an agent with a fake model; it
is plain Python that performs **the same externally visible sequence** the agent
will: await the verdict, invoke capabilities, accumulate raw responses, build
`Evidence`. That makes the barrier, the accumulation, and the sufficiency gap all
testable now, and none of those tests change when the agent arrives.

---

## 5. Types this needs

From the PoC plan, in `core/planner/models.py`:

`Evidence(results, served, failed, sufficient)` · `Result` · `Failure` ·
`Skill(id, domain, description, guidance, tool_names)` · `Identity(name, persona, boundaries)`

`Source` already exists in `core/shared/models.py`. `Ask` and `Intent.asks` come
from `feat/79`'s `core/intent/models.py` — the local `Intent` has a single
`primary_domain` and no `asks`, which is the next collision with that branch.

---

## 6. Cases

Marked **new** where nothing today covers it.

| # | Case | Tier | |
|---|---|---|---|
| C1 | The wire is camelCase end to end | conformance | new |
| C2 | `traceId` echoes the request's `transactionId` | 1 | new |
| C3 | A turn with no `transactionId` still gets a `traceId` | 1 | new |
| C4 | `cause` serializes as `null`, not omitted | 1 | new |
| C5 | `confidence` is present on every outcome | 1 | new |
| C6 | `sequenceNumber` starts at 1 | 1 | changed |
| C7 | Intent and moderation both run on one turn | 3 | new |
| C8 | Discovery runs even when moderation rejects | 3 | new |
| C9 | **No `invocation.select` happens before the verdict resolves** | 3 | new |
| C10 | A rejected turn emits no claims and invokes no capability | 3 | replaces old case 25 |
| C11 | `Evidence.results` carries one numbered `Source` per invocation | 1 | new |
| C12 | Sufficiency marks an ask that no result served | 1 | new |
| C13 | Content items cite only sources the answer declares | 1 | exists |
| C14 | `error` is populated on `unavailable` and absent otherwise | 1 | new |

**C9 is the one that matters, and it must be proven able to fail.** The PoC plan
says so directly: *"make the verdict resolve late and confirm the test catches a
tool call that does not wait for it."* Implementation: a verdict future that
records when it was awaited, and a `StubInvocation` that records when it was
called; assert the ordering. Then reorder the runner and watch the test fail.

---

## 7. Order

1. **Contract defects** (§2) — `schema.py`, `mapping.py`, `sse.py`. Pure, tested
   at tier 1, and it makes the running server correct. Do this first regardless
   of everything else.
2. `CONVENTIONS.md` casing exception, or the contradiction stands.
3. `core/planner/models.py` — `Evidence`, `Result`, `Failure`, `Skill`, `Identity`.
4. `core/planner/sufficiency.py` — real code, tier 1.
5. `ports/discovery.py`, `ports/invocation.py` + the two stub adapters.
6. `orchestration/planner.py` — the stub `plan`, and C9 first.
7. Reshape `core_runner.py` to §3, `anyio` task group per ADR-0005.
8. Composer takes `Evidence`; claims stream from its content items.

Step 1 is independent of everything after it and closes real defects, so it can
land on its own.

---

## 8. Conflicts this does not resolve

- **`Intent` shape.** `feat/79` has `Intent.asks: tuple[Ask, ...]`; the local one
  has `primary_domain` + `confidence`. Discovery and sufficiency both read
  `asks`, so this has to converge before §4 works against real discovery.
- **`UserTurn` shape.** `feat/79` has `original_query`/`enriched_query`,
  `session_id`/`transaction_id` on the turn, `UserDetails` with `phone`, and
  `ReferenceToken`. The local one has `query`, `user_id`, and no reference token.
  The `ref` question from `turn_API_contract.md` §0 is still unanswered and this
  is where it starts costing.
- **`Outcome` name collision.** `core/moderation/models.Outcome` (proceed/reject/
  clarify/no_match) versus the wire `Outcome` object (status/cause/confidence).
  Same word, two things, both in play.
- **ADR numbering.** `feat/79` holds 0003–0005 and two files numbered 0002;
  ADR-0006 was written here. A merge has to renumber something.
- Whether the runner or `plan` emits the refusal terminal (§3).
