# ADR-0005: The tool-calling agent loop lives in `orchestration/`, not behind a port

- **Status:** ACCEPTED
- **Date:** 2026-09-04
- **Deciders:** DSS code owners
- **Consulted:** Product Owner
- **Informed:** Adopter engineering teams

---

## 1. Context and Problem Statement

The planner POC (issue #10) needs a component that calls tools in a loop:
read discovery results, decide whether to call `/select`, validate the
model's arguments, call it, and decide when to stop. `core/` may not import
Pydantic AI — enforced by ruff's `TID251` plus an AST test — so this loop's
home is either `orchestration/` (which is allowed to import the framework)
or behind a new port that `core/` calls through.

The existing `LLMProvider.structured()` port is single-shot: one prompt in,
one typed object out. It has no notion of tool calling, so it cannot express
this loop as-is.

## 2. Decision Drivers

1. **`core/` must stay framework-agnostic**, per the repo's hexagonal rule.
2. **A port must be typeable without leaking the vendor SDK.** A `Protocol`
   whose parameter types are re-exported Pydantic AI types is not a real
   abstraction.
3. **Testability**, the usual reason for a port, must still hold without one.
4. **Don't build for a framework swap that has no precedent.**

## 3. Considered Options

- **Option A — the loop lives in `orchestration/planner.py`.** `core/planner/`
  holds plain-Python models, prompt building, argument validation, and
  sufficiency checking (tier 1, no framework). `orchestration/planner.py`
  constructs the Pydantic AI `Agent`, registers tools as thin wrappers that
  unpack `RunContext` and call `core/` functions, and owns the loop, timeout,
  and retry policy (tier 3, real agent + mocked ports below it).
- **Option B — a `run_with_tools(tools: ...)` port.** `core/` depends on a
  neutral tool-calling abstraction; `orchestration/` supplies a Pydantic AI
  implementation.

## 4. Decision Outcome

**Chosen option: A.**

A tool-calling port cannot be typed without leaking Pydantic AI's own
vocabulary:

- Tool schemas come from signature and docstring introspection (via griffe),
  not a shape `core/` declares.
- `RunContext` as a tool's first argument is structurally special-cased and
  stripped by the framework.
- `prepare=` takes `(RunContext, ToolDefinition) -> ToolDefinition | None` —
  both vendor types.
- `ModelRetry` is an exception the agent's own retry budget catches; a
  neutral port would need to re-declare it or import it.
- Streaming events, usage limits, and parallel tool calls have no generic
  form to abstract to.

A neutral `run_with_tools` either reimplements all of that machinery or
re-exports Pydantic AI's types under new names — at which point the "port"
depends on the framework anyway, just with extra indirection. It would
satisfy Dependency Inversion nominally (an interface exists) and violate it
substantively (the interface's whole vocabulary is the vendor's, renamed).

**Split:**

| `core/planner/` — plain Python, tier 1 | `orchestration/planner.py` — Pydantic AI, tier 3 |
|---|---|
| `models.py` — `Evidence`, `Result`, `Source`, `Skill`, `Failure` | `Agent` construction, `deps_type`, tool registration |
| `prompt.py` — `build_planner_prompt(...) -> str` | `RunContext` wrapper tools that unpack deps and call core |
| `validation.py` — argument against the pack's `filterable` | the loop, timeout, retry policy |
| `sufficiency.py` — `Evidence` × `Intent.asks` | `ModelRetry` on validation failure |
| tool bodies as plain async functions | binding tools from selected skills' `tool_names` |

This mirrors `orchestration/turn.py` today: orchestration coordinates, `core`
functions do the work.

### 4.1 Positive Consequences

- `core/planner/` stays plain Python: unit-testable with no network, no
  framework runtime, tier 1 in the testing table.
- No abstraction to maintain that exists only to be swapped — the swap it
  would enable has no observed precedent.
- Testability, the usual reason for a port, is unaffected: `FunctionModel`
  scripts exact tool-call sequences, `Agent.override` swaps the model, and
  `capture_run_messages` asserts the history — all without a network call.
  That is tier 3: real agent, mocked ports below it.

### 4.2 Negative Consequences

- `orchestration/planner.py` is coupled to Pydantic AI's `Agent` API. A future
  framework swap touches this file directly instead of one adapter behind a
  port.
- The design doc's `Plan`/Plan Executioner split is not built yet; this loop
  is a temporary stand-in behind the same seams (see the POC plan,
  `docs/.agent/plan/10-planner-agent-poc.md`).

## 5. Rejection Rationale

**Option B** looks like it satisfies the hexagonal rule but does not: every
parameter shape in `run_with_tools` would need to describe tool schemas,
`RunContext`-equivalents, retry signals, and streaming — which means either
re-implementing Pydantic AI's tool-calling machinery from scratch (a second
framework to maintain) or re-exporting its types (an interface in name only).
No case was found, across the sibling repos or elsewhere, of a team
successfully hiding an agent framework's tool-calling loop behind a stable
port through a framework swap. The observed pattern is teams dropping a
framework for a raw SDK, not swapping one agent framework for another via a
shared abstraction.

## 6. Revisit Triggers

- A second orchestration framework is actually adopted (not hypothetical) —
  re-evaluate whether the two frameworks' tool-calling models overlap enough
  for a real, non-leaky port.
- The design's `Plan` / Plan Executioner replace this loop — at that point
  `orchestration/planner.py` is deleted, not migrated behind a port.

## 7. Follow-up Actions

- **[DSS code owners]** Implement `core/planner/` and `orchestration/planner.py`
  per the split above, in the planner POC (issue #10).

## 8. Notes

- Builds on the hexagonal rule in `CLAUDE.md` (`core/` never imports the
  orchestration framework) and mirrors the existing split in
  `orchestration/turn.py`.
- Full research trail: `docs/.agent/plan/10-planner-agent-poc.md`, section
  "The loop lives in `orchestration/`, not behind a port." Primary sources:
  ai.pydantic.dev docs on agents, tools, dependencies, and testing.
