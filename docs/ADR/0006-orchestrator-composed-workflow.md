# ADR-0006: The orchestrator is a composed, code-ordered workflow of injected components

- **Status:** ACCEPTED
- **Date:** 2026-09-07
- **Deciders:** DSS code owners
- **Consulted:** Product Owner
- **Informed:** Adopter engineering teams

---

## 1. Context and Problem Statement

The DSS core components — intent, moderation, skills discovery, provider
discovery, tool discovery, planning, execution, composition, review, channel
shaping — are being built independently. Something has to hold a turn and run
them in the right order, enforce the read-only/side-effecting **barrier**
(design v2 §3), and decide which of the four ways out a turn took. Several of
these components do not exist yet (planner, executioner, composer, channel,
review); the intent, moderation and provider-discovery services do.

The question: **what shape is the orchestrator, how are components wired to it,
and how do we ship it before every component is real?**

The design doc (v2 §4) is explicit that the orchestrator is "a non-intelligent
piece of code … a mere function call which executes the workflow," that "order
is code, not config," and that composition is done "using python code," with
some components declarable optional.

## 2. Decision Drivers

1. **Order is code.** The sequence (classify → fan out → plan → execute →
   compose → shape) is the orchestrator's whole job; nothing outside it decides
   that order.
2. **Components never call each other.** Each takes plain objects and returns
   plain objects; only the orchestrator knows the sequence.
3. **The barrier is load-bearing.** Everything up to the plan is read-only and
   discardable; from execution onward a Provider call cannot be taken back. No
   side-effecting call may run until moderation has cleared.
4. **Ship the skeleton before the fleet is complete.** The workflow must wire the
   components that exist (intent, moderation, provider discovery, the planner)
   without stubbing the ones that do not — a placeholder that fetches nothing is
   untested weight and reads as "done" when it is not.
5. **Extend by configuration, not adopter code** (dss-design-v2 §4): which
   components run is decided by composition in Python, not by editing the workflow.

## 3. Considered Options

- **Option A — one `async def run_turn` over a bundle of injected component
  callables (`OrchestratorComponents`), wired once in a composition root.** The
  function expresses the order directly; each component is a plain async callable
  bound to its own model/ports/config; optional ones are left unset.
- **Option B — a pydantic-graph / framework state machine.** Model the turn as a
  graph of nodes with a shared state schema, in `orchestration/`.
- **Option C — direct imports, no injection.** `run_turn` imports each service
  and constructs its dependencies inline.

## 4. Decision Outcome

**Chosen option: A.**

- **`orchestration/orchestrator.py::run_turn(turn, components, *, now)`** is a plain
  async function returning a `TurnResult`. Its body *is* the pipeline: run intent
  and moderation concurrently, gate on the verdict, then discover providers and call
  the planner. It stops at the planner's result — response composition and channel
  shaping are separate components, not yet built, and are **not** stubbed, so the
  function does not yet stream `ChannelChunk`s.
- **Concurrency is `anyio`, not `asyncio`** (ADR-0005), consistent with the rest of
  `core`/`adapters` (e.g. `provider_discovery`). Intent and moderation run in one
  `anyio` task group.
- **Components are injected**, as a frozen `OrchestratorComponents` bundle of four
  async callables (classify, moderate, discover_providers, plan). `build_components(...)`
  is the composition root: it binds the real intent/moderation/provider-discovery
  services and the planner to their models and ports. Swapping an implementation in
  is a one-line change there, never in `run_turn`.
- **The barrier is enforced by the gate.** On any non-`PROCEED` outcome the
  orchestrator returns immediately — no discovery, no planner, so no outside call —
  and blanks the classified intent (ADR-0003). Discovery (read-only) and the planner
  run only past that gate. This replaces the earlier "pass the verdict into the
  planner as an awaitable" idea: gating up front is simpler and does no read-only
  work on a refused turn.
- **The planner owns plan creation *and* execution.** There is no separate
  executioner step in the code; the planner agent is the single downstream the
  orchestrator calls, and the only thing that touches the outside world.
- **`outcome_for(decision, plan)`** is the single place the four ways out are
  decided (design v2 §3, §6.6 table); it is the only component that sees both the
  verdict and the plan.

### 4.1 Positive Consequences

- The workflow is readable top-to-bottom and unit-testable with fake callables —
  the ports below are never reached (tier 3).
- The barrier and the four outcomes are pinned by tests independent of any
  component's real behaviour.
- The PR carries no untested placeholder weight: only real components and the
  planner contract land, so a green build means the wired path works.

### 4.2 Negative Consequences

- `run_turn` does not stream yet — it returns a `TurnResult`, not `ChannelChunk`s —
  because response composition and channel shaping are deferred to their own
  components. The streaming shape returns when those land.
- Gating before discovery means intent∥moderation and discovery are *sequential*
  rather than fully fanned out. Accepted: it costs a little latency on `PROCEED`
  turns but does zero read-only work on refused ones, and keeps the barrier obvious.

## 5. Rejection Rationale

- **Option B (framework graph)** buys nothing yet: the control flow is a linear
  pipeline with one fan-out and one barrier, no conditional re-entry or shared
  mutable state a graph would manage. It would also pull the framework into a
  module that today composes plain callables. Revisit if replanning loops or
  resumable streams (design Open #12) arrive.
- **Option C (direct imports)** makes `run_turn` un-testable without real models
  and ports, and couples the workflow to each component's construction — the
  opposite of the ports-and-adapters seam.

## 6. Revisit Triggers

- A component needs to run **conditionally** or the turn needs a **replan loop**
  (composition-local corrective retry, design §5.5) → reconsider Option B.
- **Response composition and channel shaping land** → `run_turn` grows the steps
  after the planner and changes its return to streamed `ChannelChunk`s.
- **Skills / tool discovery land** → they join the fan-out before the planner, as
  additional injected callables.

## 7. Follow-up Actions

- **[Component owners]** Build the real planner agent behind `plan_turn`'s
  signature, and the downstream response-composition and channel components; wire
  them into `run_turn`/`build_components` as they land.
- **[DSS code owners]** When moderation surfaces partial refusals, thread them
  through the planner's `Plan.refused` into composition — the field is already
  carried.
- **[DSS code owners]** Reflected in `dss-design-v2.md` §4 in this change
  (`DSS_ARCHITECTURE.md` is superseded by that doc and is not updated here).

## 8. Notes

- Builds on ADR-0003: the intent∥moderation concurrency is preserved (now an `anyio`
  task group), and a non-`PROCEED` turn still surfaces a blanked `Intent()`. The
  orchestrator sits alongside `orchestration/turn.py::run_turn` (the tested two-way
  coordinator) and extends the same idea to discovery + planning.
- The design's separate `TurnContext` is **not** introduced: the repo already
  folds `session_id`, `transaction_id`, `source_lang`, `target_lang` and `channel`
  onto `UserTurn`, and every core service already takes `UserTurn`. `now` is passed
  explicitly rather than read from the clock, matching the repo's time-threading
  convention.
