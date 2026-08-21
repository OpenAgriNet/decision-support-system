# ADR-0001: Adopt Pydantic AI as the Agentic Framework for the DSS

- **Status:** Proposed
- **Date:** 2026-08-21
- **Deciders:** OAN (OpenAgriNet) DPG architecture group
- **Consulted:** Adopter engineering leads across the sibling OAN repositories; Product Owner
- **Informed:** Adopter engineering teams; OAN DPG steward

---

## 1. Context and Problem Statement

The OAN (OpenAgriNet) DPG ships a single **Decision-Support System (DSS)** — a reasoning runtime distributed as an immutable container that each adopter runs inside their network. The DSS is the shared reasoning core; adopters extend it through mounted configuration, never by forking source.

The DSS is being **extracted from an existing production implementation** rather than written from scratch. The sibling OAN repositories run on Pydantic AI today, and their tool implementations, prompts, MCP bridges, and the `llm_core` multi-provider layer are the raw material the DSS core is assembled from. This is a decisive piece of context: the question is not "which framework would we pick on a blank page" but "what does the extraction of a shared DPG core out of working code cost, and what does each candidate framework add or destroy in the process."

Per `docs/DSS_ARCHITECTURE.md` §3, the DSS decomposes into a set of logical functions with a defined execution order. That decomposition is explicitly high-level and provisional — the architecture document carries an altitude disclaimer stating that ordering, seams, and responsibility splits are directional, that thresholds and iteration bounds are unsettled, and that every claim should be re-questioned against real behaviour during implementation. **This ADR therefore does not bind itself to the current function list, its ordering, or its cardinality.** It binds itself to the shape of the control flow, which is a separate and more stable question.

What the architecture does fix, and what the framework must therefore support:

- **Named checkpoint interception.** Policies fire at named checkpoints (moderation, pre-tool-call, post-response). This is a load-bearing primitive of the extension model (§4.1).
- **Bounded internal loops.** The planner iterates on a sufficiency judgement (§3.2); a Response Reviewer can trigger a bounded corrective re-render (§5.1, §7). Both are bounded and both are local to a single logical function.
- **Early-exit terminals.** Moderation rejection, ambiguity clarification, no-match, and Provider failure each end the turn (§7).
- **Provider neutrality.** The DSS must run against vLLM/Gemma (self-hosted), OpenAI, Azure, and Anthropic uniformly. Non-negotiable per the DPG's interoperability principle.
- **Structured, inspectable artifacts.** The plan is a first-class artifact that Policy evaluates and evidence records (§5.5); the intent object is a typed structure (§5.2). Typed structured output is not a convenience here — it is what makes the plan inspectable.
- **Node-level operational evidence.** §6.3 requires per-stage evidence without storing request or response content.

**The question.** Which agentic framework should the DSS be built on: Pydantic AI (the current implementation across the sibling OAN repos), LangGraph, or Google ADK?

### 1.1 The prior framing, and why it is reopened

An earlier draft of this ADR concluded in favour of LangGraph on the grounds that the DSS is a stateful graph with cyclic edges. Review raised three objections, and all three are accepted:

1. **The DSS layer is being extracted as a DPG from a working implementation.** Reuse of existing components is a first-order goal, not a nice-to-have.
2. **Adopters know Pydantic AI.** For a shared core that multiple adopter teams must extend, operate, and debug, the framework's familiarity across those teams is an architectural property, not a staffing detail.
3. **A graph is available inside the Pydantic ecosystem if one is needed.** `pydantic-graph` is a standalone, typed, async state-machine library that ships alongside Pydantic AI. The choice is therefore not "agent loop or graph framework" — a graph can be adopted later without leaving the ecosystem or changing the LLM substrate.

Given those, the graph question is reframed: **not "which graph framework", but "does the DSS need a graph framework at this stage at all".**

---

## 2. Decision Drivers

Ordered by weight:

1. **Reuse of the existing implementation.** The DSS is an extraction. Framework choices that discard working, production-proven components carry a cost that must be justified by a matching benefit.
2. **Provider neutrality (vLLM/OpenAI/Azure/Anthropic).** Hard requirement; violating it breaks the DPG's interoperability principle and the `llm_core` design.
3. **Fit for the control flow the architecture actually describes.** Framework capability should be matched to the flow the DSS has, with a defined path to more capability if the flow grows.
4. **Adopter familiarity and ramp-up cost.** A DPG core is extended and operated by adopter teams. Time-to-productive-contribution is an adoption property of the DPG itself.
5. **Named checkpoint interception for Policies.** Load-bearing primitive of the extension model.
6. **Typed structured output and typed dependency injection.** The plan artifact, the intent object, and the primitive schemas are all typed structures the framework should carry natively.
7. **Reversibility.** Whatever is chosen, the cost of changing it later must stay bounded. This driver is what allows the decision to be made on today's evidence rather than on a forecast.
8. **Long-term maintenance ownership.** Orchestration primitives the framework does not provide are primitives the team owns and maintains.
9. **Durable execution / checkpointed resumption.** On the future roadmap (voice concurrency, streaming resume); not a v1 commitment.
10. **Node-level observability.** A production DPG owes its adopters "which stage blocked, on which state, with which policy verdict" — not just LLM call traces.

---

## 3. Considered Options

- **Option A — Pydantic AI** (current implementation across the sibling OAN repos), with `pydantic-graph` available as an in-ecosystem upgrade path.
- **Option B — LangGraph** (Python; v1.x production line).
- **Option C — Google ADK** (Python; v1.0.0 GA August 2026; v2.x graph-native workflow engine).

---

## 4. Decision Outcome

**Chosen option: Option A — Pydantic AI.**

The DSS is built on Pydantic AI, continuing the substrate the sibling OAN repositories already run on. No graph framework is adopted at this stage.

The reasoning is that **the control flow the architecture currently describes is a sequence of logical functions with bounded loops inside individual functions and early-exit terminals between them.** A framework whose defining strength is arbitrary cyclic topology is priced for a problem the DSS does not currently present. Pydantic AI carries the flow the DSS has today — sequential composition, bounded retry, early return — in ordinary Python, while providing natively the things the architecture genuinely leans on: typed structured output for the plan and intent artifacts, typed dependency injection, provider neutrality, and per-step tool exposure control.

The value of this option is not only what it avoids. It is that **the extraction proceeds by moving working components into a new boundary rather than by reimplementing them against new primitives.** The tool implementations, prompts, MCP bridges, and `llm_core` provider layer that the sibling repos have already proven in production carry across intact. For a DPG whose entire distribution model rests on a single immutable image that many adopters run, the shortest credible path to a trustworthy v1 has weight of its own.

**This decision is explicitly provisional on the flow staying broadly linear, and the flow is expected to be re-questioned.** The architecture document says as much about its own logical decomposition, and this ADR inherits that posture rather than pretending to more certainty than the design has. §4.2 defines the upgrade path and §4.4 the triggers for taking it.

### 4.1 Positive Consequences

- **The existing implementation is reused rather than rewritten.** The extraction is a boundary change, not a reimplementation.
- **Zero framework ramp-up for adopter teams.** Contribution to and operation of the shared core start from existing knowledge.
- **Provider neutrality is preserved unchanged.** `llm_core`'s multi-provider routing, fallback, and circuit-breaking carry over as-is.
- **Typed structured output and typed DI stay first-class**, which is what makes the plan artifact (§5.5) inspectable and the primitive schemas (§4.1) enforceable.
- **The architecture is free to evolve without a framework commitment pinning it.** Because the logical decomposition is expected to change, not adopting a topology-shaped framework now avoids encoding a provisional structure into the substrate.
- **Effort goes into DSS-specific problems.** Policy checkpoint semantics, the plan schema, sufficiency evaluation, the redaction interceptor, and evidence emission are the DPG's differentiating work; none of them is solved by a framework choice.

### 4.2 Upgrade path — `pydantic-graph`, if and when orchestration warrants it

If orchestration complexity grows past what sequential composition carries cleanly, the DSS adopts **`pydantic-graph`** — a standalone, typed, async graph and state-machine library that ships alongside Pydantic AI and does not depend on it. Pydantic AI's own agent execution is built on it, and `agent.iter()` exposes that graph for step-by-step driving.

This is recorded now, before it is needed, because a pre-identified upgrade path is what makes today's decision safe:

- **Same ecosystem, same substrate.** Adopting it changes how logical functions are composed. It does not change the LLM layer, the provider layer, the tool layer, the prompts, or the typed contracts.
- **Same idiom.** Nodes are typed classes carrying mutable run state; edges are inferred from `run()` return-type annotations; a cycle is a node returning an earlier node. Teams fluent in Pydantic AI are not learning a second mental model.
- **Incremental, not all-at-once.** Individual logical functions become nodes as they earn it. There is no flag day.
- **It brings the graph-shaped affordances with it** — named nodes as checkpoints, structural per-node evidence, renderable diagrams of the real runtime flow, and cycles where cycles are genuinely warranted.

One limitation is recorded honestly so it is not discovered late: within `pydantic-graph`, the newer builder API that provides parallel fan-out, mapping, and joins does **not** provide native state persistence, and directs durability to an external durable-execution backend (Temporal, DBOS, Prefect, and Restate are officially supported by Pydantic AI). The older node-based API does provide native persistence. A future requirement combining **parallel fan-out with checkpointed resume** is therefore the one shape that costs more here than under a framework bundling both — see §4.4 and §6.2.

### 4.3 Framework boundary — keep the framework out of the core

The DSS is implemented as a **framework-agnostic core with framework usage confined to a thin orchestration layer.** All DSS logic — intent recognition, enrichment, moderation, skill discovery, persona and context composition, planning, execution, review, response composition, channel shaping — is plain Python operating on plain domain objects. The orchestration layer sequences these functions and is the only code permitted to import the agentic framework.

This rule is what makes §4.2 an upgrade rather than a migration, and it is what would keep a move to any other framework scoped to the orchestration layer instead of the whole DSS. It also keeps the core unit-testable without a framework runtime, and insulates the codebase from framework churn.

The boundary is not a convention to be remembered. It is enforced in CI as an import rule, per §7.

### 4.4 Revisit triggers

This ADR is reopened when **any** of the following becomes true:

1. **Mid-execution replanning is admitted to the DSS.** `DSS_ARCHITECTURE.md` §8.3 currently holds this open and leans against it. Admitting it introduces a genuine cycle between execution and planning and is the single strongest signal that graph-shaped orchestration has arrived.
2. **The logical decomposition stops being broadly sequential.** The architecture expects its own §3 to change. If that change introduces routine cross-function cycles or conditional re-entry rather than bounded in-function loops, the premise of this decision has expired.
3. **Multi-domain fan-out and checkpointed resume are both committed.** Either alone is manageable; together they are the combination §4.2 flags as costliest under this option.
4. **Bounded loops stop being bounded.** If sufficiency iteration or corrective re-render acquires cross-function scope, or if their orchestration outgrows readable Python, the abstraction is being hand-built and should be adopted instead.
5. **Node-level evidence proves unreliable without structural support.** If §6.3 evidence emission drifts from what actually executes because it is hand-instrumented rather than structural, that is a real defect in a DPG's accountability obligations, and it is one `pydantic-graph` fixes structurally.

Triggers 1, 2, and 4 point to §4.2. Trigger 3 is the one warranting a fresh comparison against LangGraph and ADK.

### 4.5 Negative Consequences

- **Orchestration is team-owned code.** Sequencing, bounded loops, and checkpoint invocation are application code the DSS maintains. This is accepted because at the current flow shape that code is small and legible; §4.4 exists precisely to detect when it stops being either.
- **Durable execution is not bundled.** If checkpointed resume becomes a requirement, it arrives via an external backend rather than an inline checkpointer. This is deferred work, not avoided work.
- **Node-level evidence is hand-instrumented at this stage.** It must be deliberately maintained alongside the flow rather than falling out of the structure. Tracked as trigger 5.
- **A second decision point is accepted by design.** Choosing not to adopt a graph now means possibly adopting one later. This is a deliberate trade: a bounded, pre-scoped change at a point of evidence, in place of an unbounded commitment made on a forecast.
- **If the flow does become genuinely graph-shaped soon**, some orchestration written now is discarded. §4.3 bounds that loss to the orchestration layer.

---

## 5. Pros and Cons of the Options

### 5.1 Option A — Pydantic AI

**Pros**
- The existing implementation is reused; the extraction is a boundary change rather than a reimplementation.
- Provider neutrality is first-class; `llm_core` already implements multi-provider routing with fallback and circuit breakers.
- Type-safe DI and structured output are load-bearing today and stay first-class — directly serving the plan artifact and intent object.
- Per-step tool exposure control and per-call approval gating are available natively, without leaving a run.
- Adopter teams already know it; ramp-up is zero, which for a DPG core is an adoption property.
- Officially supported durable-execution integrations exist (Temporal, DBOS, Prefect, Restate) when durability is needed.
- `pydantic-graph` provides an in-ecosystem, incremental upgrade path if orchestration grows.
- The container/config extension model is framework-neutral, so nothing about the mounted-`/config` design depends on this choice.

**Cons**
- Sequencing, bounded loops, and checkpoint invocation are application code the team owns.
- Durable execution and checkpointed resume are not bundled; they arrive via an external backend.
- Node-level evidence is hand-instrumented rather than structural at this stage.
- If the flow becomes genuinely graph-shaped, a second adoption step is required — mitigated by §4.2 and §4.3.

### 5.2 Option B — LangGraph

**Pros**
- Native fit for arbitrary cyclic topologies, with conditional edges and cross-node state as core primitives.
- Durable execution and checkpointing are bundled, with an inline checkpointer.
- Large ecosystem for cyclic agent patterns, with substantial production usage.
- Node-level observability via LangSmith or OTel instrumentation.
- Provider-neutral; does not couple to any specific LLM vendor.
- Framework owns the orchestration abstractions rather than the team.

**Cons**
- Requires rewriting the DSS orchestration layer against new primitives at the moment the DPG is being extracted from working code.
- Introduces a second framework and a second mental model for adopter teams who are productive in Pydantic AI today.
- Its defining strength — arbitrary cyclic topology — is not what the currently described flow needs.
- DX step down on typed DI ergonomics relative to Pydantic AI.
- Fast-moving release cadence; version pinning required.

### 5.3 Option C — Google ADK

**Pros**
- Python 1.0.0 GA'd August 2026; production-marked.
- Graph-native workflow engine in the 2.x line: edges, routing, parallel execution, join nodes, nested workflows, resumable processes.
- SDK-level provider neutrality via LiteLLM plus native support for Gemini, Gemma, Claude, Ollama, vLLM, and OpenAI, satisfying the DPG interoperability principle.
- Strong multimodal support, relevant if voice/image/video ever move into the DSS core.
- Native MCP integration.

**Cons**
- Same rewrite cost as LangGraph, with the same timing problem against an extraction.
- Same second-framework cost for adopter teams.
- Younger ecosystem for the cyclic patterns that would be the reason to adopt it.
- Managed-runtime pull toward Vertex Agent Engine; not binding for self-hosted OAN, but a directional consideration.
- Vendor-alignment optics absent a formal GCP partnership.

---

## 6. Rejection Rationale

### 6.1 Why LangGraph is not chosen

Not for lack of capability. LangGraph is a strong framework and would carry the DSS competently. It is not chosen for two reasons, in order of weight.

**First, and decisively: the rewrite cost lands at exactly the wrong moment.** The DSS is being extracted from a working Pydantic AI implementation into a DPG core. Choosing LangGraph converts that extraction from "move proven components behind a new boundary" into "reimplement proven components against unfamiliar primitives while simultaneously establishing a new boundary." Two hard changes at once, on the artifact the entire DPG distribution model depends on. The cost is not only the rewrite itself but the loss of the confidence that comes from shipping code with production history behind it. Every adopter team also pays a ramp-up tax on a framework none of them currently uses — and for a DPG, adopter ramp-up is not a private engineering cost, it is a property of the good being distributed.

**Second: the flow described today is broadly linear.** The loops the architecture specifies — sufficiency iteration in planning, corrective re-render in composition — are bounded and local to a single logical function. The failure paths are early-exit terminals, not cycles. LangGraph's defining advantage is arbitrary cyclic topology with durable state across it, and that advantage is not currently being drawn on. Paying a full rewrite for capability the flow does not exercise is the wrong trade at this stage.

**This second reason is explicitly held open.** The architecture's logical decomposition is early-stage and expected to change; §4.4 records what change would reopen this. Framework capability should track the flow's real shape rather than a forecast of it, in either direction. If the flow becomes genuinely cyclic, §4.2 provides an in-ecosystem answer first, and trigger 3 in §4.4 identifies the specific shape — parallel fan-out combined with checkpointed resume — that would instead warrant a fresh comparison against LangGraph on its merits.

### 6.2 Why Google ADK is not chosen

ADK is a genuine peer of LangGraph for this problem. Python 1.0.0 is GA, the 2.x graph-native workflow engine covers the graph shapes under discussion, and provider neutrality via LiteLLM plus native vLLM/Gemma/Claude/OpenAI support satisfies the DPG interoperability principle for a self-hosted deployment.

It is not chosen for the same primary reason as LangGraph: **it requires the same rewrite, at the same wrong moment, with the same second-framework cost to adopter teams.** The graph-versus-linear argument in §6.1 applies identically. Where ADK differs from LangGraph is in a younger ecosystem for the cyclic patterns that would justify adopting it, and a managed-runtime pull toward Vertex Agent Engine that is not binding for OAN's self-hosted model but is a directional consideration for a DPG that must remain neutral about where adopters run it.

ADK would warrant reconsideration if **either** of the following became true:
1. The org made a strategic commitment to GCP/Vertex/Gemini as the OAN DPG's standard stack.
2. Multimodal (voice/image/video) moved into the DSS core, making ADK's multimodal strength load-bearing.

Absent both, and with the rewrite argument standing, ADK is not adopted.

---

## 7. Follow-up Actions

- **[Owner: DSS CODE OWNERS]** Enforce the framework boundary in §4.3 as a CI import rule: the framework-agnostic core must not import the agentic framework.
- **[Owner: DSS CODE OWNERS]** Keep the orchestration layer's sequencing, bounded loops, and checkpoint invocation in one clearly identified module, so §4.4's triggers are observable in a single place rather than diffused across the core.
- **[Owner: DSS CODE OWNERS]** Instrument §6.3 node-level evidence deliberately, and track whether it drifts from the executed flow — this is the observable form of §4.4 trigger 5.
- **[Owner: OAN DPG steward]** Re-evaluate this ADR against §4.4 at each release-planning cycle, and whenever `DSS_ARCHITECTURE.md` §3 or §8.3 changes materially.

---

## 8. Notes

- This ADR is anchored on the OAN DPG target architecture in `docs/DSS_ARCHITECTURE.md`, not on the current per-adopter topology.
- The architecture's logical-function decomposition is early-stage and explicitly expected to change. This ADR deliberately does not bind to that decomposition's contents; it binds to the shape of the control flow, and §4.4 states what change to that shape would reopen the decision.
- The decision rests on evidence available today, with §4.2's upgrade path and §4.3's framework boundary jointly ensuring that changing it later stays a bounded change to the orchestration layer.
- Framework choice does not decide the state schema, DI approach, plan schema, or observability integration. Those are separate ADRs.
