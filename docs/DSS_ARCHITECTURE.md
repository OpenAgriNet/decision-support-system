# DPG Architecture — Decision Support System (DSS) on OpenAgriNet

## Purpose

This document describes the target architecture for the OpenAgriNet **Decision Support System (DSS)** — the Experience Layer module this repository implements. It fixes the DSS's boundary, responsibilities, interfaces, and extension model.

Actors, layers, interaction types, and principles are defined in the OpenAgriNet Architecture Overview and are not restated here.

---

## 1. Decision Support System — purpose and boundary

The DSS is an **Experience Layer module** used when an interaction requires interpretation, policy-guided reasoning, tool orchestration, or response composition. It receives a normalized and permitted request from the Experience API, selects permitted tools or Provider capabilities, and returns a reviewed response to Experience.

**The DSS invokes Provider capabilities only through the Network Consumer Adapter.** It is not a separate architecture layer, a Network Exchange service, or a Provider.

### 1.1 Responsibilities

1. Recognise the actor's intent and map it to one or more capability needs.
2. Apply moderation and policy checkpoints before invoking tools or Provider capabilities.
3. Compose the configured persona, permitted context, skills, and tools for the interaction.
4. Select the minimum relevant and permitted local tools or Provider capabilities.
5. Coordinate tool and capability calls through stable interfaces.
6. Compose and review the response, including provenance, confidence, limitations, and next steps where applicable.
7. Return response content, status, and delivery instructions to the Experience API.
8. Emit non-personal operational evidence for routing, policy, dependency, review, and failure decisions.

### 1.2 What the DSS never does

- Authenticate a person, store credentials, or own the participating deployment's user mapping.
- Own durable session, profile, preference, subscription, or notification state.
- Terminate channel protocols or own telephony, messaging, HTTP, streaming, callback, or notification delivery.
- Operate Discovery or Registry, maintain the authoritative catalog index, or resolve Provider endpoints, keys, schemas, and participation status.
- Sign, verify, correlate, or deliver Network protocol messages.
- Own Provider knowledge, vector stores, records, workflows, obligations, or domain results.
- Invent a Provider result or silently change the meaning of a Provider response.
- Persist or log personal payloads, authorization artifacts, application references, prompts containing personal data, or raw conversations.

---

## 2. DSS interfaces

### 2.1 Provides

- A structured intent and capability need.
- Requests to permitted local tools or to the Network Consumer Adapter.
- A reviewed response with presentation intent, status, provenance, limitations, and escalation information.
- Non-personal stage, dependency, policy, review, and failure evidence.

### 2.2 Requires

- A normalized request and permitted session context from the Experience API.
- Versioned persona, policy, skill, tool, and reviewer configuration.
- A capability-search and Provider-invocation interface supplied through the Network Consumer Adapter.
- Declared response, error, timeout, retry, confidence, and escalation contracts.
- Context providers that enforce their own authority and disclosure boundaries.

---

## 3. DSS logical functions

The DSS decomposes into the following logical functions. Each has a single purpose.

1. **Moderation and policy checks** decide whether an interaction may proceed.
2. **Intent recognition** converts the request into a structured capability need without binding it to a channel.
3. **Enrichment** uses previous historical context to sharpen the intent. May run in the same LLM call as intent recognition or as a separate step; the choice is an implementation decision.
4. **Routing** selects the smallest relevant set of permitted skills, tools, and Provider capabilities.
5. **Persona and context composition** applies configured behaviour and only the context permitted for the interaction.
6. **Execution** coordinates tool calls and Provider-capability invocations until a result, accepted status, failure, or escalation condition is reached.
7. **Response composition and review** produces an understandable result without changing Provider-owned meaning.
8. **Channel response** shapes the reviewed content for the target channel (structured data, text, voice, media hints).

> **Shape distinction inside Routing.** Skills are injected into the prompt as reasoning guidance. Tools and Provider capabilities are exposed to the LLM as function-call schemas (name + description + input schema) that the LLM may invoke inside Execution.

### 3.1 Bridging tools and Provider capabilities

From the LLM's point of view, MCP tools and Network-backed Provider capabilities are indistinguishable tool calls with typed inputs and outputs. The DSS translates:

- **Discovered Provider capabilities** (from `on_discover` responses cached locally by the Network Consumer Adapter) → synthetic tool definitions (name + description + input schema) exposed to the LLM.
- **Local MCP tools** (from `list_tools` on the client MCP server) → tool definitions exposed to the LLM.

When the LLM decides to invoke:
- **Local MCP tools** — DSS calls MCP `call_tool` directly, feeds the result back.
- **Provider-backed tools** — DSS delegates to the Network Consumer Adapter, which handles Registry resolution, signing, correlation, and callback handling. DSS never speaks Network itself.

---

## 4. Extension model

The DSS is extended by **configuration**, not by code. Adopters compose extensions by mounting YAML/Markdown files that instantiate a fixed set of primitives; the runtime behaviour of each primitive is defined by the DSS itself. This keeps every extension inside the DSS boundary by construction — an adopter cannot introduce a new state, failure, security, or scaling boundary through configuration.

Code-level extensions (custom reasoning engines, sidecar model providers, alternative tool-protocol servers) are out of scope for this document and will be defined separately.

### 4.1 Configuration primitives

The DSS accepts five primitives, mounted as YAML/Markdown files under `/config/`:

| Primitive | Add | Override | Disable | Purpose |
|-----------|-----|----------|---------|---------|
| **Identity** | Yes | Yes | No | Establish who the assistant is for a given tenant. One active per tenant. Name, role, domain scope. Tone and refusal patterns live in Policies. |
| **Skills** | Yes | Yes | **Yes** | Cross-cutting reasoning patterns injected as prompt text. Routed per turn; only relevant skills enter the prompt. Not for per-tool instructions. |
| **Policies** | Yes | Yes | No | Enforceable guardrails at named checkpoints (Moderation, Pre-tool-call, Post-response). LLM-evaluated or deterministic. Follow the policy's `on_violation` action on rejection. |
| **Context Providers** | Yes | **No** | No | Declarative mapping from prompt variable → source (`request.user_context.*`, `builtin://clock.date`, …). Override by using a different variable name. |
| **Response Reviewers** | Yes | Yes | No | Post-hoc quality/safety checks on candidate answers (length, language conformance, presence of citations, …). May merge with post-response Policies in a later refactor. |

**Personas.** A Persona is a *composition label* referencing a bundle of primitives (Identity + Skill set + Policy pack + Reviewers). Not a separate primitive; a way to bundle for a tenant.

**Skill disable.** The DSS may ship more skills than any single tenant needs. A tenant lists skills to switch off in `/config/skills.yaml`:

```yaml
disable:
  - dairy_advisory
  - milk_pouring_lookup
```

Disabled skills are removed from routing entirely — they consume no prompt tokens, no embedding lookup, and no taxonomy slot for that tenant. Every shipped skill declares a `disableable` flag in its frontmatter (default `true`); safety-critical skills may set `disableable: false` and CI validation rejects attempts to disable them. For Policies and Reviewers, override already covers the "turn it off" case by declaring a no-op body — a separate disable mechanism is not offered.

### 4.2 Adopter-authored configuration validation

- **JSON Schemas** for each primitive are published at a stable URL by the DPG steward.
- Adopters run standard validators (`ajv`, `check-jsonschema`) against `/config/` in their CI, independent of DSS reachability.
- **Skill-specific lint** enforces: description non-trivial; at least one worked example; routing metadata non-empty; body under a token limit; referenced `{{VARS}}` match declared Context Providers; test cases declared and passing.
- **Runtime catalog validation.** The DSS validates incoming Provider catalogs against Network Exchange schemas as they arrive. Catalogs failing validation are dropped and logged; they are not surfaced to the LLM.

---

## 5. Runtime specifics (implementation choices)

The sections below refine implementation decisions this repo makes on top of the DSS logical-function baseline. They are **not** architectural mandates — an alternative implementation may substitute equivalent behaviour that preserves the same interface, evidence, privacy, and Provider-accountability contracts.

### 5.1 Request envelope and language handling

The DSS receives every turn as a structured envelope from the Experience API. **Language is a first-class field on the envelope**, not a projected Context Provider variable — it is a channel/session property, not user-profile data.

**Envelope shape (directional; concrete class locked during v1 design).**

```python
class UserDetails:
    user_id: str | None = None
    phone: str | None = None

class UserTurn:
    query: str
    session_id: str
    source_lang: str          # language the user spoke/typed
    target_lang: str          # language the response should come back in
    channel: str              # web / voice / sms / whatsapp / ...
    user: UserDetails
    history: list             # typed shape deferred (see §8)
    response_max_chars: int | None = None
```

**Why source and target are separate.**
- `source_lang` drives inbound reasoning: Routing filters skills whose `supported_languages` don't include it; Intent Recognition routes through a language-appropriate embedding model; Provider capabilities can be down-ranked if they don't serve this language.
- `target_lang` drives outbound reasoning: Persona composition may hint the LLM to draft in `target_lang`; a shipped `language_conformance` Response Reviewer verifies the candidate response is in `target_lang` and can trigger a corrective retry on drift.

**Translation is not a DSS responsibility.** Where translation is used, it is a conditional Experience-Layer adapter (or Provider-side capability). Any component that decrypts, translates, maps, or otherwise processes personal fields becomes an explicit personal-data processor — see §6.

**Two supported operating modes** (chosen per tenant via Identity's `reasoning_language`, if set):
- **Translate-then-reason (English-canonical DSS).** Experience Layer translates to `en` before the DSS sees it. `source_lang` on the envelope records what the user actually spoke, for routing purposes.
- **Reason-in-native (multilingual DSS).** DSS reasons in `source_lang`. Response drafted directly in `target_lang`.

**Adopter obligation on language.** Every Skill declares `supported_languages` in frontmatter; every Provider capability descriptor declares `response_languages`. Both default to `[*]` if omitted. Routing uses these to filter/rank against the envelope's `source_lang` and `target_lang`.  - Defered for now we shall come back to this later.


### 5.2 Intent-based routing

The DSS uses **intent-based routing**: extract an intent once, then match uniformly against skills, tools, and Provider capabilities.

**Intent object (directional; concrete schema is v1 design work):**
```
{
  primary_domain: "milk_collection",
  secondary_domains: ["dairy"],
  entities: { mobile: "...", week: "..." },
  action_type: "lookup" | "advisory" | "transaction",
  confidence: 0.87
}
```

**Layered extraction (v1 direction).** Each layer is cheaper than the next; the pipeline stops at the first layer that returns a confident intent. The layers, in order:

1. **Session cache.** In-session lookup keyed by `(session_id, normalised_query)`. Hits the case where a user repeats or lightly rephrases the same question inside one conversation. Constant time, no model call, no embedding lookup.
2. **Global frequency cache.** Cross-session cache of the most frequently seen `normalised_query → intent` mappings for this tenant. Populated from earlier LLM-classified intents. Bounded size, LRU eviction, TTL to protect against taxonomy drift. Constant time.
3. **Pattern / regex match.** Handcrafted rules for high-confidence deterministic cases — mobile numbers, explicit slash-commands, canonical Provider names, structured queries the adopter knows about. Declared in tenant config; runs before any model is loaded. Deterministic.
4. **Embedding similarity.** Query embedding matched against artifact embeddings (built from descriptions + example queries + tagged domains). Fast, cheap.
5. **LLM classifier fallback.** If similarity confidence is low or the query is ambiguous, a small LLM call determines intent using artifact metadata. The result is written back to the global frequency cache so the next occurrence is served from layer 2.

Each layer emits its outcome (`hit` / `miss` / `low_confidence`) to operational evidence so the tenant can see the cache-hit ratio and where LLM cost is being spent.

**Taxonomy.** The DSS ships a base taxonomy (`dairy`, `finance`, `weather`, `agri-scheme`, `livestock-health`, `soil-health`, `crop-advisory`, `mandi-prices`, …). Adopters extend locally. The DPG governs the taxonomy centrally — extensions that become widely used get promoted; deprecated tags follow a versioning lifecycle. The same taxonomy is used at **knowledge ingestion** so RAG retrieval respects the same intent structure that routing uses.

**Adopter obligation:**
- Every MCP tool declaration includes a domain tag and a description.
- Every Provider catalog includes a capability descriptor (domain + intent patterns + example queries).
- Every Skill frontmatter declares its domain and example queries.
- These are enforced by schema validation.

### 5.3 Deployment model 

- Distributed as a versioned container image (`agent-core:X.Y.Z`).
- Adopters pull the image, run it in their network alongside the Experience API, mount their `/config` volume.
- Same immutable image across all adopters and versions.
- Extension happens through mounted config, not code changes.

### 5.4 Local catalog cache 

The DSS embeds a **catalog cache** subscribed to Discovery updates through the Network Consumer Adapter. The cache reduces per-turn Discovery lookups (routing uses cached catalogs), refreshes on TTL and subscription push, and invalidates on Discovery change notifications. Cache ownership and freshness rules are open items (§8).

---

## 6. Personal data, security, and operational evidence

### 6.1 PII posture -- Needs more discussions.

**Shared DSS processing does not receive raw personal data.** Personal payloads required by a declared Provider capability follow the protected direct Experience-to-Provider path and are not inserted into prompts, tool registries, shared context stores, logs, traces, or analytics.

- A **Provider-scoped subject reference** — opaque to everyone except the intended Provider — may be transported by Experience and both network adapter edges without being persisted or logged by intermediaries.
- **Data-use authorization** (a compact signed artifact or a resolvable reference) states what a named Provider may do, for a declared purpose, with permitted data categories, permitted operation, validity, and revocation status. The intended Provider validates it before resolving the reference or processing protected fields.
- If a deployment enables an extension that decrypts, translates, maps, or otherwise processes personal fields, that extension is an **explicit personal-data processor**. Its purpose, data categories, retention, isolation, authorization proof, and evidence obligations must be declared separately.

### 6.2 Redaction interceptor (implementation choice)

Any DPG code path that writes to durable storage — logs, traces, telemetry payloads, metrics, session snapshots, error dumps, audit records — must route through a **redaction interceptor** before the write lands. Redaction is enforced at the **sink layer** (logger, tracer, telemetry emitter), not per component. A DSS component may log a message that references raw PII; the sink strips or pseudonymises it before persistence. This makes the rule non-bypassable by construction.

- **Baseline rules ship with DSS.** Phone / mobile numbers and Aadhaar-like patterns covered out of the box.
- **Adopter-extensible.** Adopters declare additional patterns (farmer IDs, land-record numbers, coordinates precise enough to identify a plot, jurisdiction-specific identifiers) via mounted config.
- **Redaction vs pseudonymisation.** Baseline is redaction (`phone=***`). Adopters may opt fields into pseudonymisation (`phone=usr_a1b2c3`) when stable trace-correlation across a session is needed without leaking the raw value.
- **Consequence.** Persisted artifacts observable by the DPG — Langfuse traces, application logs, telemetry, on-disk error dumps — never contain raw PII. Correlation across a session is preserved through pseudonymous tokens where declared.

Concrete redaction-interceptor design (library integration vs sink processor, wire format for pseudonymisation tokens, adopter rule schema, per-sink coverage) is a v1 implementation detail — tracked in §8.

### 6.3 Operational evidence

The DSS emits non-personal evidence events for every turn:

- Random interaction identifier and DSS stage.
- Selected capability and dependency class.
- Policy, moderation, routing, and response-review outcome.
- Tool, adapter, model, persona, policy, and reviewer versions where applicable.
- Latency, timeout, retry, cancellation, escalation, and terminal outcome.
- Confidence or quality category **without storing the request or response content**.

---

## 7. Failure behaviour

- **Unsafe or disallowed requests** return a controlled rejection without invoking downstream capabilities.
- **Ambiguous requests** ask for clarification before a capability is invoked.
- **No suitable capability** returns a no-match outcome rather than a fabricated answer.
- **Dependency and Provider failures** preserve the Provider status and follow the declared retry or escalation policy.
- **Low-confidence or reviewer-rejected responses** are qualified, retried, or escalated according to policy.
- **A DSS failure does not change Provider-owned state or hide an accepted Provider obligation.**

---

## 8. Assumptions and open items

### 8.1 Assumptions (what the DSS relies on from its neighbours)

- The Experience API validates the deployment assertion and supplies only permitted interaction context.
- The **Network Consumer Adapter** owns Discovery queries, Registry resolution, schema validation, signing, verification, correlation, and callback handling.
- Each Provider remains authoritative for its capability, workflow state, and result.
- Persona, policy, tool, reviewer, and model configurations are versioned and can be traced for operational review.

### 8.2 Implementation choices, not architectural mandates

The architecture is deliberately silent on these; this repo makes explicit choices, and alternative implementations may substitute equivalent behaviour:

- **ReAct** is one possible reasoning strategy — this repo uses it. An implementation may use another reasoning strategy or a deterministic workflow while preserving the same DSS contract.
- **MCP** is one possible local-tool integration protocol — this repo uses it. An implementation may use another tool protocol.
- The logical functions may run in one process or in separately deployed modules. Deployment choices do not change the Experience Layer boundary or move Provider and Network Exchange responsibilities into the DSS.

### 8.3 Open items

DSS-scoped, deferred to v1 design and later governance:

- **Minimum DSS contracts** — request, response, tool, context, evidence, error.
- **Which deployment profiles require a DSS**, and which may use a deterministic Experience implementation.
- **Translation boundary and personal-data classification.**
- **Ownership and freshness rules for the Experience-side catalog cache.**
- **Evaluation thresholds, confidence categories, and human-escalation requirements.**
- **Conformance tests** that prove an alternative reasoning engine or tool adapter preserves the DSS contract.
- **Exact primitive schemas.** Direction locked; concrete schemas designed with the first prototype.
- **Skill routing algorithm.** Trigger-first-then-LLM is the v1 direction; details tuned during prototyping.
- **Response Reviewers vs post-response Policies.** May unify or stay separate.
- **Router scope.** Whether Skills go through the same Router as tools/Providers.
- **Voice-channel specifics.** Concurrent moderation patterns and voice-specific latency budgets.
- **Registry of MCP tool schemas.** Currently spec/docs contracts only; promote to Schema Registry later if cross-adopter interop needs emerge.
- **Redaction interceptor implementation.** §6.2 fixes the PII posture and sink-layer enforcement model. Open: library integration vs sink processor, pseudonymisation-token wire format, adopter rule-schema shape, per-sink coverage.
- **Request envelope `history` typing.** Concrete `TurnHistoryEntry` shape (roles, tool-call trace inclusion, redaction posture) deferred.
- **`UserDetails` extensibility.** Whether tenant-specific profile fields (farmer ID, region, land size) attach through an open `extra` dict on `UserDetails` or route through Context Providers projecting from a separate `user_context` payload. Leaning toward the latter.
- **PII posture — DSS envelope and forwarding rules.** The DPG architecture prescribes "shared DSS processing does not receive raw personal data" (Posture A). This repo's §5.1 envelope currently carries `user_id` and `phone`, and §6.2 implies raw PII may transit DSS with sink-layer redaction as the primary control (Posture B). Open questions: (1) which fields belong on `UserTurn` — session/interaction IDs and Provider-scoped opaque references only, or also raw identifiers? (2) does the DSS see free-text `query` when the query itself carries PII (names, addresses spoken by the user), and if so, is pre-DSS scrubbing an Experience-layer responsibility or a DSS one? (3) if PII may enter DSS in-flight, do we need per-Provider forwarding allowlists (which fields flow to which capability) in addition to sink-layer redaction? (4) how does personalisation ("Hi Ramesh…") work when DSS can't see the name — templated response with post-DSS substitution by the participating deployment, or opaque user-segment tokens? Needs discussion before v1 envelope is locked.

---

## 9. Related documents
- Model Context Protocol specification — https://modelcontextprotocol.io (implementation choice, §8.2).
