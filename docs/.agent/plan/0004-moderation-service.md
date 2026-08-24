# 0004 — Moderation service

**Issue:** #57 · **Status:** spec under review · **PR C of three**

Depends on [`0003-policy-schema.md`](./0003-policy-schema.md) — this evaluates the
policies that change loads and validates.

## Context

This is the change that makes moderation actually *do* something. 0002 defined what a
farmer is asking for; 0003 defined the rules; this evaluates them and returns a
verdict.

**The problem it fixes is the most serious finding from the sibling repos: two of
three do not enforce moderation at all.**

`bharat-oan-api/app/services/chat.py:466`:

```python
moderation_data = await _run_moderation(user_message, session_id)
deps.update_moderation_str(str(moderation_data))
```

There is no branch on `category`. The verdict is injected into the agent's prompt as
prose, and enforcement is a *sentence in a system prompt*
(`assets/prompts/agrinet_en.md:32` — *"Proceed only if the query is classified as
Valid Agricultural… Moderation decisions are final"*). So an `unsafe_illegal` verdict
still reaches the tool-calling agent, which then decides for itself whether to comply.

`mh-oan-api` is the same, and worse in one respect: its `except Exception` swallows
moderation failures and continues (`app/services/chat.py:45-78`), so the turn proceeds
with moderation silently skipped. That path is reachable in normal operation —
`max_tokens=32` truncates the JSON response it's asking for.

Only `amul` has a real gate (`app/services/chat.py:553`).

A prompt-level guardrail is also precisely what `role_obfuscation` attacks target: it
asks the model to self-police against instructions engineered to make it not.

**So: enforcement here is structural. Code, not prose.**

---

## Scope

**In:** the deterministic evaluator, the LLM-evaluated path, `ModerationDecision` and
its enums, the capability check, failure handling, and the enforcement barrier's
contract.

**Out:** the intent service itself (classification is a separate slice), response
composition, and the `pre_tool_call` / `post_response` checkpoints.

---

## The decision type

### `src/dss/core/moderation/models.py`

```python
class Outcome(StrEnum):
    PROCEED = "proceed"
    REJECT = "reject"        # we will not answer this
    CLARIFY = "clarify"      # we did not understand — ask
    NO_MATCH = "no_match"    # understood; nothing here serves it
```

**Four outcomes, and the fourth is the one that gets lost.** Three different things
happen when a question goes unanswered, and they look identical if lumped together:

| what happened | what we say | example |
|---|---|---|
| we **won't** answer | a refusal | "how do I fake an insurance claim?" |
| we **didn't understand** | a question back | a garbled or ambiguous query |
| we **can't yet** | "not available yet" | "today's mandi price", before lookup exists |

If a mandi-price question is refused like a harmful one, nobody can answer *"how many
real farmer questions are we turning away because we haven't built that feature?"* —
the number is buried in with the spam. That question is the roadmap.

It also matters across deployments: this system runs in several, and one must not
permanently refuse a question another could answer. *"We can't do that here"* is
temporary; *"we refuse that"* sounds permanent.

```python
class ReasonCode(StrEnum):
    # harm — a policy fired
    UNSAFE_ILLEGAL = "unsafe_illegal"
    ROLE_OBFUSCATION = "role_obfuscation"
    POLITICAL_CONTROVERSIAL = "political_controversial"
    EXTERNAL_REFERENCE = "external_reference"
    # scope and comprehension — not harm
    DOMAIN_UNMAPPED = "domain_unmapped"
    INTENT_LOW_CONFIDENCE = "intent_low_confidence"
    UNSUPPORTED_ACTION_TYPE = "unsupported_action_type"
    # infrastructure — not the farmer's fault
    MODERATION_UNAVAILABLE = "moderation_unavailable"
    # any adopter-defined policy; violated_policy_id says which
    ADOPTER_POLICY = "adopter_policy"
```

The three groups are the point: **harm** is a refusal, **scope** is a capability gap,
**infrastructure** is our failure. Collapsed, they all read as "rejected" — which is
exactly why the sibling repos' metrics can't distinguish them.

```python
class ModerationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: Outcome
    reason_code: ReasonCode | None = None
    violated_policy_id: str | None = None
    unsupported: list[ActionType] = Field(default_factory=list)

    @model_validator(mode="after")
    def reason_required_unless_proceed(self) -> ModerationDecision: ...

    @model_validator(mode="after")
    def unsupported_only_when_proceeding(self) -> ModerationDecision: ...
```

The two validators, in one line each: **a non-proceed outcome must say why**, and
**a partial answer is only possible on a turn that proceeds**.

| outcome | `reason_code` | `violated_policy_id` | `unsupported` |
|---|---|---|---|
| `PROCEED`, fully served | None | None | empty |
| `PROCEED`, partly served | None | None | what we can't do |
| `REJECT` / `CLARIFY` | required | the policy that fired | empty |
| `NO_MATCH` | `UNSUPPORTED_ACTION_TYPE` | None — capability check, not a policy | empty |
| `REJECT` on timeout | `MODERATION_UNAVAILABLE` | None — no policy fired | empty |

The timeout row is why `reason_code` and `violated_policy_id` are separate fields: a
timeout must still tell the farmer *"something went wrong, please try again"* rather
than *"your question was rejected"*, and there is no policy to attribute it to.

### Harm never partitions

If a query violates a harm policy, **the whole turn is refused.** There is no partial
anything once harm fires.

Rather than trusting evaluation order to remember that, the second validator makes it
structural: `unsupported` may be non-empty **only** when `outcome=PROCEED`. Harm
always yields `REJECT`, so a partially-served harmful turn is unconstructable.

Partial fulfilment therefore applies to **scope gaps only** — "we understood, we just
haven't built that" — never to harm, comprehension, or infrastructure.

### How an adopter policy reports its reason

`reason_code` is derived from the policy id (0003), and `ReasonCode` is a closed DSS
enum — so an adopter policy has nothing to derive from. The tension is that
`reason_code` was serving two consumers with opposite needs: evidence wants a
**closed** vocabulary for cross-deployment comparison; adopter debugging wants an
**open** one.

`violated_policy_id` already *is* the open-vocabulary answer, so nothing new is
needed. An adopter policy contributes `ADOPTER_POLICY` plus its namespaced id:

```python
ModerationDecision(
    outcome=Outcome.REJECT,
    reason_code=ReasonCode.ADOPTER_POLICY,
    violated_policy_id="amul/local-banned-pesticides",
)
```

Metrics group by `reason_code`; an adopter debugging their deployment reads
`violated_policy_id`. Both served, and an adopter cannot mint DSS vocabulary because
the enum has no member for them to claim.

---

## Evaluation

### Order: deterministic first, then one LLM call

```
moderation checkpoint
  ├── deterministic policies → evaluated in Python, in precedence order
  │     first violation wins and short-circuits
  └── llm policies → signals + examples rendered into ONE prompt
        one call returns which policy, if any, was violated
```

Deterministic runs first deliberately: it is free and exact, so a query already
rejected on a null check never costs a model call.

One batched LLM call rather than one per policy. Per-policy calls would multiply both
cost and the timeout exposure below by N — and the evidence from the siblings is that a
*single* 8 KB prompt is already at the edge of staying coherent, so more prompts is
not obviously more accurate either.

### The capability check — not a policy

Supported-action scope is computed, not declared:

```python
unsupported = [a for a in intent.action_types if a not in settings.supported_action_types]
```

- all unsupported → `NO_MATCH`, `reason_code=UNSUPPORTED_ACTION_TYPE`
- some unsupported → `PROCEED` with `unsupported=[...]`
- none → `PROCEED`

0003 explains why this can't be a policy: a policy has one `on_violation`, and this
needs two behaviours. It's also the right home — scope is *"no match against the
reachable capability set"*, which is what stops one deployment permanently refusing
what another could answer.

`settings.supported_action_types` is a stopgap for the Network Consumer Adapter's
reachable-capability view.

---

## Failure handling

**Retries, then fail closed.** A configurable fallback model is **out of scope here** —
see below.

`reason_code` separates `MODERATION_UNAVAILABLE` from a policy violation, so a
transient failure produces *"something went wrong, try again"* rather than *"your
question was rejected"*. Per-policy `fail_mode` allows the justified exception.

### The fallback model is not built, and one doc implies it is

An earlier draft of this section read *"retries, then a cheap fallback model, then fail
closed"* — which overstated what this change delivers. There is no fallback setting, no
YAML key, and no test for a fallback transition. This change retries, then fails closed.

**Read this before assuming otherwise:** ADR-0001 states that `llm_core`'s "multi-provider
routing, fallback, and circuit-breaking carry over as-is" (§4.2, and again in
Consequences). That describes an inherited *capability*, not something this change wires
up. Anyone reading the ADR alone would conclude moderation already degrades gracefully to a
cheaper model. It does not.

Deferred deliberately, and the gap is worth naming precisely, because the requirement is
broader than failure handling: config should decide the fallback when a model *fails, is
too slow, or is too costly*. Cost- and latency-driven demotion is a different trigger from
error, and neither is built. It needs a model registry to reference models by name, and an
endpoint setting so a local model can be addressed at all — neither of which exists yet.
Its own story.

### Why the orchestrator does not judge on failure

A considered alternative was: on failure after retries, let the planner decide whether
to proceed, based on the intent. Rejected, for a reason that is decisive rather than
stylistic:

**On failure there is no verdict to judge.** The orchestrator would be deciding
*"moderation didn't run — do I proceed?"* using `Intent` as its evidence. But 0002
deliberately moved harm judgment *out* of intent: `Intent` carries domains, action
types, entities, confidence — and **no harm signal at all**. So it could only ever
conclude "looks like a normal agriculture query, proceed." That is fail-open with
extra steps.

And it fails exactly where it matters: a `role_obfuscation` attempt phrased as a crop
question classifies as `crop-advisory`, because that is what it looks like. Telling
them apart is moderation's job — and moderation is the thing that just failed.

Three further problems:

- **Non-deterministic on the failure path.** Same query, same failure, different
  outcomes across runs — the hardest class of bug to reproduce, sitting on the safety
  path.
- **It's an attack surface.** If failure hands control to a model reading
  attacker-influenced text, then *causing* failure becomes a strategy. An oversized
  query that reliably times out the call is cheap to construct.
- **It contradicts the architecture doc.** §7 requires unsafe requests return a
  controlled rejection *without invoking downstream capabilities*; §4.1 says follow the
  policy's `on_violation`. Neither has a "when the check fails, ask the orchestrator"
  branch.

---

## Enforcement: await before execute

**The barrier sits between plan construction and plan execution.**

```
UserTurn
  → enrichment (stub)                 → EnrichedQuery
  → intent                            → Intent
  → moderation ─────────────┐ (async)
  → skills / tools / plan   │ (parallel, cancellable)
                            ▼
                      ⟨ BARRIER ⟩  await the verdict
              reject │ clarify │ no_match │ proceed
                                             → execute the plan
```

Moderation is dispatched, then downstream work — skill selection, tool selection, plan
construction — proceeds in parallel. Before the plan *executes*, the verdict is
awaited. On reject, in-flight internal work is cancelled.

**Why this line.** It tracks exactly where reversibility ends: plan-*built* is
internal computation and cancellable; plan-*executed* is not. So racing plan
construction against moderation costs only wasted tokens on the reject path, never a
side effect.

### What may and may not be cancelled

| in flight | on reject | why |
|---|---|---|
| skill selection, tool discovery, plan construction | **cancel** | internal, reversible, no external state |
| anything dispatched through the Network Consumer Adapter | **must not be assumed cancellable** | §7: a DSS failure must not change Provider-owned state or hide an accepted obligation |

Cancelling your side of a dispatched Provider call does not un-send it. **This is
precisely why the barrier sits before execution** — no Provider call should be in
flight at that point. Cancellation is a token-saving optimisation on internal work,
never a safety mechanism.

**Where the barrier must never move.** A later argument of the form *"let the first
tool call start, it's read-only"* should be refused: policy must be re-evaluated per
execution (it can depend on bound entities, context, time), and read-only-ness isn't
knowable before the adapter resolves the capability.

### Why not let the planner weigh the verdict

Considered and rejected. It makes enforcement a model judgment on the path where
determinism matters most, and it is what the two non-enforcing repos already do.

An allowed-but-notable verdict *can* still reach the planner — as a **Context Provider
variable** (§4.1). Useful reasoning context, structurally separate from enforcement, so
the enforcement path has exactly one implementation.

**Scope for this change:** there is no planner and no side-effecting tool yet, so the
barrier is trivially "before returning the outcome". The shape is committed to now
because retrofitting it later means touching every producer and consumer.

---

## The LLM adapter

One `LLMProvider` port in `ports/`, implemented in `adapters/llm/`. `core/` never
imports the framework — enforced by the boundary check from PR #4.

Per-function model binding rather than one global model. Both `bharat` (a separate
`MODERATION_MODEL` at `temperature=0.0`) and `amul` (`pipeline.example.yaml`, where
moderation is a first-class pipeline step with its own model, endpoint, timeout, and
fallback chain) converged on the same signal independently: moderation is
high-volume, low-token, latency-critical classification, and shouldn't share a binding
with composition.

Settings where the three repos disagree — the survey found they disagree on *every*
knob — become config with a DSS default:

| knob | bharat | mh | amul | default |
|---|---|---|---|---|
| model | separate | shared | per-run | **separate** |
| temperature | 0.0 | 0.1 | 0.1 | **0.0** — a judgment, not a generation |
| timeout | 20s env | 5s | 5s | **5s**, env-overridable |
| retries | 2 | 2 | 0 | **1** |

Structured-output mode (`PromptedOutput` / `NativeOutput` / bare — all three differ)
is **not** a config field: it's a Pydantic AI detail that must stay inside
`adapters/`, since `core/` can't know it exists.

---

## Language

English only for this slice. Translation stays outside the DSS boundary.

`source_lang` and `target_lang` remain first-class on `UserTurn` and flow through —
they are **not** dropped as unused. Dropping them now means re-threading them through
every signature later.

**One caution from `amul`, worth naming as a rule.** Its moderation prompt is dense
with Gujarati terms (`ભાવફેર`, `બોનસ`, `ડિવિડન્ડ`) while its pipeline delivers
*pre-translated English* — so those examples rarely match anything. The prompt was
written for one input language and the pipeline supplies another.

**Rule: the language a prompt's examples are written in must be the language the
prompt actually receives** — and that pairing should be asserted, not assumed. A
tier-5 structural test checks it rather than trusting it.

Consequence to accept knowingly: the accuracy corpus is English-only, so its number
must be labelled `en` and never read as a general claim.

---

## Tests

| tier | what | gate |
|---|---|---|
| 1 — unit | policy evaluation over fixed `Intent` + context, mocked `LLMProvider` | every commit |
| 4 — golden | ~15 cassette-recorded cases spanning outcomes and boundaries | every commit |
| 5 — structural | output shape only: valid enum, `violated_policy_id` resolves, prompt language matches input language | every commit |
| 6 — eval | a fresh accuracy corpus, scored | **scheduled only — never a merge gate** |

Tier 1 carries the bulk, and legitimately: most of this change is policy evaluation
over a structured input, which is plain Python needing no model at all. The LLM call is
one component, not the system.

### On bharat's 205 fixtures

`bharat-oan-api/tests/fixtures/` has 175 single-turn and 30 multi-turn labelled cases
— the only ground truth that exists, and `mh`/`amul` have none.

They are labelled against **bharat's 8 categories**, a taxonomy this design no longer
has: classification moved to intent, and moderation emits no category. So relabelling
175 cases against `(intent, decision)` is arguably more work than writing fresh ones,
and produces a corpus contorted to fit its old shape.

**Write fresh; mine the old for scenarios.** The boundary cases encode expensive field
learning — the bribery `role_obfuscation` case, the Aluminum Phosphide case, the
scheme-acronym confusions — and those *situations* should reappear even though the
labels don't port.

The 30 multi-turn cases become the **enrichment** test set when enrichment lands.
Worth copying somewhere now rather than rediscovering them later.

### Red-green order

| # | red test | makes green |
|---|---|---|
| 1 | `ModerationDecision(outcome=REJECT)` with no reason raises | the decision type |
| 2 | `unsupported` non-empty with `outcome=REJECT` raises | harm-never-partitions |
| 3 | a deterministic policy on a null field fires | the evaluator |
| 4 | first violation short-circuits, later policies don't run | precedence |
| 5 | an adopter policy yields `ADOPTER_POLICY` + its own id | reason derivation |
| 6 | all actions unsupported → `NO_MATCH` | capability check |
| 7 | some unsupported → `PROCEED` + `unsupported` | partial fulfilment |
| 8 | a mocked LLM timeout → `MODERATION_UNAVAILABLE`, fail closed | failure handling |
| 9 | `fail_mode: open` on timeout → proceeds | the exception path |
| 10 | evaluation never calls the LLM when a deterministic policy fires | ordering |

Step 10 matters for cost, not just correctness: it is the test that stops the free
path silently regressing into a paid one.

---

## Verification

```bash
uv run pytest                    # tiers 1, 4, 5
uv run pytest -m eval            # tier 6, deliberately
```

Then the behaviour that can't be asserted from a unit test — throw adversarial queries
at it and read the verdicts. Moderation quality is learned that way, not from test
output. Worth a throwaway CLI for this; explicitly *not* the entrypoint decision,
which needs its own ADR.

---

## Follow-ups

- **`unsupported` has no renderer.** A `PROCEED` carrying `unsupported=[LOOKUP]` needs
  response composition to say *"I can't look up prices yet"*. Until then the field is
  produced and validated but unread — and an unread `unsupported` is a silent
  half-answer, the exact failure it exists to prevent. **Whichever slice adds response
  composition owes this.**
- **The rejection message.** This change produces a `reason_code`, not user-facing
  text. What the farmer actually reads on a reject stays open until the channel
  function lands — deliberate layering, but worth being explicit rather than surprised.
- **Architecture doc**: §3.0's ordering (it currently argues moderation precedes
  intent), the enforcement model, and the four-outcome contract. Plus whether the
  reordering and parallel fan-out trip ADR-0001's revisit trigger on the decomposition
  no longer being broadly sequential.
- **`pre_tool_call` and `post_response`** checkpoints, and the generic dispatcher —
  which 0003 deliberately does not build from a single instance.
