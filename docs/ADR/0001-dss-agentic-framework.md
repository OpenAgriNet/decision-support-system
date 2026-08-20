# ADR-0001: Adopt LangGraph as the Agentic Framework for the DSS

- **Status:** Proposed
- **Date:** 2026-08-18
- **Deciders:** OAN (OpenAgriNet) DPG architecture group
- **Consulted:** Adopter engineering leads across the sibling OAN repositories; Product Owner
- **Informed:** Adopter engineering teams; OAN DPG steward
---

## 1. Context and Problem Statement

The OAN (OpenAgriNet) DPG ships a single **Decision-Support System (DSS)** — a reasoning runtime distributed as an immutable container that each adopter runs inside their network. The DSS is the shared reasoning core; adopters extend it through mounted configuration and plugins, never by forking source.

Per `docs/DSS_ARCHITECTURE.md` §3, the DSS is not a single-agent-with-tools application. It decomposes into eight logical functions with a defined flow:

```
Moderation → Intent Recognition → Enrichment → Routing → Persona Composition
→ Execution → Response Composition & Review → Channel Response
```

The architecture requires:

- **Named checkpoint interception.** Policies fire at moderation, pre-tool-call, and post-response checkpoints.
- **Corrective retry loops.** Response Reviewers can trigger a retry with a correction hint on violation.
- **Dynamic tool exposure.** During Execution, the LLM may discover it needs a tool set that was not selected at initial routing time; the graph must be able to re-invoke Routing and augment the exposed tools.
- **Intent re-check.** Routing may find zero matches and need to re-invoke Intent Recognition with a hint.
- **Provider neutrality.** DSS must run against vLLM/Gemma (self-hosted), OpenAI, Azure, and Anthropic uniformly. Non-negotiable per the DPG's interoperability principle.
- **Roadmap items** that extend the graph shape further: voice-channel concurrent moderation, multi-domain query fan-out, streaming interruption/resumption with checkpointing.

**The question.** Which agentic framework should the DSS be built on: Pydantic AI (the current implementation across the sibling OAN repos), LangGraph, or Google ADK?

---

## 2. Decision Drivers

Ordered by weight, drawn from the OAN DPG architecture and roadmap:

1. **Fit for a stateful graph with cycles.** The DSS is a multi-node graph with cyclic edges (intent re-check, mid-loop re-routing, corrective retry). This is the shape of the problem, not an implementation detail.
2. **Provider neutrality (vLLM/OpenAI/Azure/Anthropic).** Hard requirement; violating it breaks the DPG's interoperability principle and the `llm_core` design.
3. **Named checkpoint interception for Policies.** Load-bearing primitive of the extension model.
4. **Corrective retry and dynamic tool exposure.** Required for production quality; both are cyclic graph edges.
5. **Durable execution / checkpointed resumption.** On the v2 roadmap (voice concurrency, streaming resume). Framework support removes it from the team's build list.
6. **Framework maturity for standardising multiple production adopter systems.** Alpha or unstable SDKs are not acceptable substrates for a shared OAN DPG core.
7. **Ecosystem maturity for the required patterns.** Published patterns for corrective retries, dynamic tool selection, interrupt/resume, and reflection loops reduce team-owned code.
8. **Long-term maintenance ownership.** Every orchestration primitive the framework does not provide is one adopter teams must own and maintain.
9. **Team velocity and learning curve.** Ramp-up cost across adopter teams matters.
10. **Node-level observability.** A production OAN DPG owes its adopters "which node blocked, on which state, with which policy verdict" — not just LLM call traces.

Note: an earlier draft included "strategic alignment (Google/GCP partnership)" as a driver. It has been removed — no such strategic commitment is on the table, so it cannot inform this decision.

---

## 3. Considered Options

- **Option A — Pydantic AI (current implementation across the sibling OAN repos)**
- **Option B — LangGraph** (Python; v1.0 GA October 2025, v1.2 released May 2026)
- **Option C — Google ADK** (Python; v1.0.0 GA August 5, 2026; v2.x-alpha graph-native workflow engine in flight)

Full pros/cons for each are below in §5.

---

## 4. Decision Outcome

**Chosen option: Option B — LangGraph.**

The DSS as specified in `docs/DSS_ARCHITECTURE.md` is a stateful graph with cyclic flows, named policy checkpoints, and corrective retries. LangGraph is designed for exactly that shape, and its 1.x line has been production-stable since October 2025 with published patterns for every cyclic case the DSS needs. The migration cost is a **one-time investment in the shared DSS core** that all adopters consume — it is not "rewriting multiple production systems." Every future resilience or streaming-resume feature the roadmap adds becomes a graph edit, not a sprint of hand-rolled orchestration.

Pydantic AI would require the team to hand-build LangGraph's abstractions (conditional edges, state mutation across nodes, interrupt/resume, reflection loops) inside application code that adopter teams would maintain forever. This trades short-term velocity for long-term maintenance liability across the OAN DPG.

Google ADK is a genuine peer of LangGraph as of August 2026 — Python 1.0.0 is GA, ADK 2.0 shipped a graph-native workflow engine (edges array, join nodes, resumable processes) in April 2026, and provider neutrality via LiteLLM plus native vLLM/Gemma/Claude/OpenAI support satisfies the DPG interoperability principle at the SDK level. LangGraph wins on ecosystem maturity for the specific cyclic patterns the DSS needs (reflection loops, dynamic tool exposure, checkpointed resume), on the volume of published production examples, and on the direct mapping between DSS Policy checkpoints and `interrupt_before` / `interrupt_after`. The margin is narrower than earlier framings suggested; see §6 for the honest comparison.

### 4.1 Positive Consequences

- **Architecture and framework align.** The DSS logical-function graph maps directly onto LangGraph nodes and edges. Policy checkpoints map onto `interrupt_before` / `interrupt_after`. Reviewer retry maps onto conditional edges. The graph model in the architecture doc is the graph model in code.
- **Durable execution and checkpointing are built in.** Voice concurrency, streaming resume, and cross-restart continuity become configuration, not implementation. Durable state — an agent run surviving a server restart — is a core LangGraph 1.x guarantee.
- **Ecosystem answers common patterns.** Reflection loops, dynamic tool selection, interrupt/resume, corrective retries are all published LangGraph patterns with production users at scale (Klarna, LinkedIn, Uber, Replit).
- **Node-level observability out of the box.** LangSmith (or OTel-instrumented LangGraph) shows exactly which node produced which state transition.
- **Provider neutrality preserved.** `llm_core`'s multi-provider design ports over unchanged.
- **Costs are paid once in the shared core**, not repeatedly across adopter systems.

### 4.2 Negative Consequences

- **Full rewrite of the DSS orchestration layer.** Tool implementations, prompts, ONIX/MCP bridges, and LLM provider wrappers port over; the agent loop and DI plumbing do not. Estimated ~8–12 weeks for 2 engineers to reach parity with the current per-adopter agent loop.
- **Learning-curve tax.** ~4 weeks per team for engineers new to graph-shaped thinking. "Think in graphs" is not marketing — it is a real cognitive shift.
- **DX regression on typed DI.** LangGraph's state schema is Pydantic-based, but the ergonomics of "this tool receives this typed context" are cleaner in Pydantic AI. Typed-context patterns will be rebuilt on the state schema and will work, but the DX is a step down.
- **LangChain-the-org ships fast.** API-churn risk is real. LangGraph 1.x has stabilised meaningfully, but minor releases still introduce behavioural changes to streaming and event types. Version pinning is required.
- **Reversal cost.** If LangGraph turns out wrong, the framework-boundary rule in §4.3 scopes the reversal to the orchestration layer, not the whole DSS.
- **ADK 2.x graph-native engine is a genuine alternative** whose ecosystem is younger but growing. If ADK closes the ecosystem gap during the DSS bring-up, this decision is worth revisiting before v2.

### 4.3 Framework boundary — keep LangGraph out of the core

The DSS is implemented as a **framework-agnostic core with LangGraph confined to a thin orchestration layer**. All DSS logic — moderation, intent recognition, enrichment, routing, persona composition, execution, reviewers, response composition, channel shaping — is plain Python returning plain domain objects. The orchestration layer wraps each logical function as a graph node, defines the edges, and is the only code that imports LangGraph.

This makes reversal cheap: swapping frameworks is a rewrite of the orchestration layer, not the DSS. It also keeps the core unit-testable without a graph runtime, and insulates the codebase from framework churn.

---

## 5. Pros and Cons of the Options

### 5.1 Option A — Pydantic AI

**Pros**
- Zero migration cost from the current implementation across the sibling OAN repos.
- Provider neutrality is first-class; `llm_core` already implements multi-provider routing with fallback and circuit breakers.
- Type-safe DI and structured output are load-bearing today and stay first-class.
- Adopter teams already know the framework; ramp-up cost is zero.
- Container/config extension model is framework-neutral, so nothing about the OAN DPG's mounted-`/config` design depends on this choice.

**Cons**
- No native graph/state-machine primitive. Every cyclic flow in §1 must be built as orchestration code around `agent.run()` / `agent.iter()`.
- Durable execution, checkpointing, and interrupt/resume are hand-rolled on Redis. Adequate for the current app; needs significant investment when voice concurrency and streaming resume land.
- `agent.run_stream()` assumes a fixed tool set for the run. Dynamic tool exposure mid-Execution requires breaking the loop and starting a new run — rebuilding LangGraph's core abstraction in application code.
- Debugging is loop-shaped, not graph-shaped. Traces show LLM calls, not node-by-node state transitions.
- Adopter teams own the orchestration layer forever. Every LangGraph primitive becomes application code they must maintain.

### 5.2 Option B — LangGraph

**Pros**
- Matches the DSS shape natively: nodes are DSS logical functions, edges are the flow described in the architecture, policy checkpoints are `interrupt_before` / `interrupt_after`, Reviewer retry is a conditional edge.
- Durable execution and checkpointing are built in — the v2 roadmap items (voice concurrency, streaming resume) become configuration.
- **Largest ecosystem for cyclic agent patterns.** Reflection loops, dynamic tool selection, interrupt/resume, corrective retries are all published and battle-tested (Klarna, LinkedIn, Uber, Replit run production LangGraph).
- Node-level observability via LangSmith or OTel-instrumented LangGraph.
- Provider-neutral; does not couple to any specific LLM vendor. `llm_core` ports over.
- Framework owns the orchestration abstractions — adopter teams maintain graphs, not orchestration primitives.
- v1.0 GA'd October 2025; v1.2 is current (May 2026). Backward compatibility maintained across 1.x.

**Cons**
- Full rewrite of the DSS orchestration layer against LangGraph primitives (~8–12 weeks for 2 engineers to parity).
- ~4 weeks per team of learning-curve tax on graph-shaped thinking.
- DX regression vs. Pydantic AI on typed DI ergonomics.
- LangChain-the-org ships fast; minor releases can introduce behavioural changes to streaming and event types. Version pinning is required.
- If the DSS surprisingly stays linear, the LangGraph tax bought nothing (§1 argues this is unlikely).

### 5.3 Option C — Google ADK

**Pros**
- **Python 1.0.0 GA'd August 5, 2026.** Production-marked, not beta.
- **Graph-native workflow engine in ADK 2.x.** Edges arrays, routing logic, parallel execution, join nodes, nested workflows, resumable processes. Approximate parity with LangGraph for the DSS's graph-shape needs.
- SDK-level provider neutrality via LiteLLM (100+ providers) plus native support for Gemini, Gemma, Claude, Ollama, vLLM, OpenAI. Satisfies the DPG interoperability principle at the SDK layer.
- Best-in-class multimodal (voice/image/video) if that migrates into the DSS core later.
- Native MCP integration; less bridging code than Pydantic AI or LangGraph.
- Software-engineering ethos (versioned, testable, extensible agents) aligns with the DPG's five-primitives design.

**Cons**
- **Ecosystem is younger than LangGraph** for the specific cyclic patterns the DSS needs. Fewer published examples of reflection loops, corrective retries, and dynamic tool exposure at production scale.
- **Managed-runtime lock-in on Vertex Agent Engine** if adopters choose that hosting path. Not binding for OAN's self-hosted deployment model, but a footgun if any adopter migrates to Vertex.
- **2.x line is still stabilising.** Choosing the 2.x graph-native engine buys the shape but takes on maturation risk that 1.x-line users avoid.
- Full rewrite cost, same magnitude as LangGraph.
- Vendor-alignment optics if the org has no formal Google/GCP partnership.

---

## 6. Rejection Rationale

### 6.1 Why Pydantic AI is rejected

Not for lack of merit — the current OAN codebases prove Pydantic AI can carry a serious production workload. Rejected because:

- The DSS is a stateful graph, not a single agent. Choosing Pydantic AI means rebuilding LangGraph's abstractions in application code.
- The v2 roadmap (voice concurrency, checkpointed streaming resume) turns the "hand-roll it later" plan into a multi-quarter investment adopter teams share.
- Long-term maintenance ownership sits with adopter teams instead of the framework.

Pydantic AI becomes the right choice only if the team consciously decides to keep the DSS mostly linear in v1, defer corrective retries and dynamic tool exposure to v2, and accept the hand-rolled orchestration cost when they land. This is a defensible bet — but a bet that the roadmap will stay simpler than the architecture doc describes.

### 6.2 Why Google ADK is rejected

As of August 2026, the case against ADK rests on a narrower base than earlier framings suggested. Being explicit about what's changed:

**What no longer holds:**
- ADK Python is **not** beta — v1.0.0 GA'd August 5, 2026.
- Provider neutrality is **not** meaningfully weaker at the SDK level. LiteLLM + native vLLM/Gemma/Claude/OpenAI is roughly on par with what `llm_core` gives you today. The DPG interoperability principle is not violated for a self-hosted OAN deployment.
- Graph shape is **not** meaningfully weaker. ADK 2.0's graph-native workflow engine (April 2026) covers edges arrays, join nodes, and resumable processes — approximate parity with LangGraph for the DSS's graph-shape needs.

**What remains true:**
- **Ecosystem maturity for cyclic patterns.** LangGraph has years of production users (Klarna, LinkedIn, Uber, Replit) and published patterns for the specific cyclic cases the DSS needs. ADK's cyclic-pattern ecosystem is younger and less battle-tested for reflection loops, corrective retries, and dynamic tool exposure at scale.
- **Direct Policy-checkpoint mapping.** LangGraph's `interrupt_before` / `interrupt_after` map onto the DSS Policy checkpoints (moderation / pre-tool-call / post-response) with less impedance than ADK's current alternatives.
- **Managed-runtime lock-in.** If any adopter migrates to Vertex Agent Engine, they inherit GCP-locked hosting. Not binding for self-hosted OAN, but a directional consideration.

**Net.** The case for LangGraph over ADK is **ecosystem maturity for the specific cyclic patterns the DSS uses, plus a cleaner mapping onto Policy checkpoints**. Both frameworks would carry the DSS competently. If ADK's ecosystem closes the gap during the DSS bring-up — and the direction of travel suggests it will — this decision is worth revisiting before v2. The framework boundary in §4.3 keeps that reversal cheap.

ADK would become the outright right choice if **either** of the following became true:
1. The org made a strategic commitment to GCP/Vertex/Gemini as the OAN DPG's standard stack.
2. Multimodal (voice/image/video) moved into the DSS core, making ADK's multimodal strength load-bearing.

Absent both, LangGraph's ecosystem edge — narrow but real — carries the decision today.

---

## 7. Follow-up Actions

- **[Owner: DSS CODE OWNERS]** Enforce the framework boundary in §4.3 in the DSS core repo; keep `langgraph` imports out of the framework-agnostic core.
- **[Owner: OAN DPG steward]** Track LangChain/LangGraph API-churn risk; adopt a version-pinning policy in ADR-0003 or in the DSS core repo's release notes.
- **[Owner: OAN DPG steward]** Re-evaluate ADK ecosystem maturity for cyclic patterns before v2 planning; if the gap has closed, reopen this ADR.

---

## 8. Notes

- This ADR is anchored on the OAN DPG target architecture in `docs/DSS_ARCHITECTURE.md`, not on the current per-adopter topology. The earlier comparison document optimised for zero migration cost against today's app; this ADR optimises for fit against the target the OAN DPG is being built to reach.
- The cyclic-flow argument in §2 is the decisive driver. If a future review concludes the DSS will remain linear in practice, this ADR should be revisited — the LangGraph investment is only worth it if the graph shape is real.
- Framework choice does not decide the state schema, DI approach, or observability integration. Those are separate ADRs to be written after the prototype.
