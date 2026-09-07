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
4. **Ship before the fleet is complete.** The workflow must run end-to-end today,
   with placeholders standing in for unbuilt components behind their design-doc
   contracts.
5. **Extend by configuration, not adopter code** (DSS_ARCHITECTURE §4): optional
   components must be switchable without editing the workflow.

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

- **`orchestration/orchestrator.py::run_turn(turn, components, *, now)`** is an
  async generator yielding `ChannelChunk`s. Its body *is* the pipeline: start
  moderation, `await classify`, fan out skills/providers/tools with
  `asyncio.gather`, build the plan (which awaits the moderation verdict at the
  barrier), gate on the outcome, then execute → compose → [review] → shape.
- **Components are injected**, as a frozen `OrchestratorComponents` bundle of
  async callables plus `identity`. `build_components(...)` is the composition
  root: it binds the real intent/moderation/provider-discovery services to their
  models and ports and fills the rest with placeholders. Swapping a real
  implementation in is a one-line change there, never in `run_turn`.
- **The barrier is enforced by control flow.** The moderation task is handed to
  the planner as an `Awaitable` and awaited there, at the last moment; the
  orchestrator then reads the same verdict and, on any non-`PROCEED` outcome or an
  empty plan, emits **one** terminal chunk and returns — before `execute` is ever
  called. Only `ANSWERED` streams.
- **`status_for(decision, plan)`** is the single place the four ways out are
  decided (design v2 §3, §6.6 table); it is the only component that sees both the
  verdict and the plan.
- **Optional components are unset, not disabled by a flag inside the workflow.**
  `review` is `Callable | None`; unbound, it is skipped and a startup log warns
  that grounding violations will not be detected (design v2 §6.9). Moderation has
  no such switch — it is mandatory.
- **Placeholders honour the contracts, not the behaviour.** Each unbuilt component
  ships as a `core/<function>/` package with the design-doc models and a
  deterministic stand-in service, clearly marked PLACEHOLDER. They fetch nothing
  and reason about nothing; they exist so the workflow runs and so the real
  component drops in behind an unchanged signature.

### 4.1 Positive Consequences

- The workflow is readable top-to-bottom and unit-testable with fake callables —
  the ports below are never reached (tier 3).
- The barrier and the four outcomes are pinned by tests independent of any
  component's real behaviour.
- A component team can develop against its `core/<function>/models.py` contract
  and swap its `service.py` in without touching orchestration.

### 4.2 Negative Consequences

- On a rejected turn the fan-out (including a read-only provider-discovery hop)
  has already run and is thrown away. Accepted per the design's barrier reasoning
  — read-only work may cross the barrier — and it keeps the common `PROCEED` path
  off the critical latency of a serial moderation-then-discovery ordering.
- `run_turn` reuses `asyncio` (`ensure_future`/`gather`) rather than anyio
  (ADR-0005). The moderation-as-awaitable-passed-to-the-planner pattern is
  naturally future-shaped, and this matches the existing `orchestration/turn.py`.
  Revisit if the turn grows structured-concurrency needs (cancellation scopes
  around Provider calls).

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
- **Resumable streams** (Open #12): buffering claims against `trace_id` changes
  the composer/channel seam and may change what `run_turn` yields.
- The terminal (non-`PROCEED`) paths need localized, channel-shaped wording →
  route them through compose/shape instead of the fixed `_TERMINAL_TEXT`.

## 7. Follow-up Actions

- **[Component owners]** Replace each placeholder `core/<function>/service.py`
  (skills, tool_discovery, planning, execution, composition, channel, review) with
  the real implementation behind the same signature.
- **[DSS code owners]** When moderation surfaces partial refusals
  (`ModerationVerdict.refused` in the design), thread them through the planner's
  `Plan.refused` into the composer — the field is already carried.
- **[DSS code owners]** Reflected in `DSS_ARCHITECTURE.md` §3 in this change.

## 8. Notes

- Builds on ADR-0003: the intent/moderation fan-out is preserved; the orchestrator
  generalizes it into the full pipeline rather than replacing
  `orchestration/turn.py::run_turn` (which remains the tested two-way coordinator).
- The design's separate `TurnContext` is **not** introduced: the repo already
  folds `session_id`, `transaction_id`, `source_lang`, `target_lang` and `channel`
  onto `UserTurn`, and every core service already takes `UserTurn`. `now` is passed
  explicitly rather than read from the clock, matching the repo's time-threading
  convention.
