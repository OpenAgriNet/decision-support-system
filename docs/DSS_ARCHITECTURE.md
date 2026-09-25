# DPG Architecture — Decision Support System (DSS) on OpenAgriNet

## Purpose

This document describes the target architecture for the OpenAgriNet **Decision Support System (DSS)** — the Experience Layer module this repository implements. It fixes the DSS's boundary, responsibilities, interfaces, and extension model.

Actors, layers, interaction types, and principles are defined in the OpenAgriNet Architecture Overview and are not restated here.

---

## 1. Decision Support System — purpose and boundary

The DSS is an **Experience Layer module** used when an interaction requires interpretation, policy-guided reasoning, tool orchestration, or response composition. It receives a normalized and permitted request from the Experience API, selects permitted tools or Provider capabilities, and returns a reviewed response to Experience.

**The DSS invokes Provider capabilities only through the Network Consumer Adapter.** It is not a separate architecture layer, a Network Exchange service, or a Provider.

### 1.1 Responsibilities

1. Recognise the actor's intent and map it to one or more capability needs, resolving references against prior interaction context.
2. Apply moderation and policy checkpoints before invoking tools or Provider capabilities.
3. Compose the configured persona, permitted context, skills, and tools for the interaction.
4. Select the minimum relevant and permitted local tools or Provider capabilities.
5. Plan and coordinate tool and capability calls through stable interfaces, and judge whether the gathered result is sufficient to answer the need.
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
- A reviewed response with presentation intent, status, provenance, limitations, and escalation information — delivered whole, or released in pieces as it is composed (ADR-0011).
- Non-personal stage, dependency, policy, review, and failure evidence.

### 2.2 Requires

- A normalized request and permitted session context from the Experience API.
- Versioned persona, policy, skill, tool, and reviewer configuration.
- A capability-search and Provider-invocation interface supplied through the Network Consumer Adapter.
- Declared response, error, timeout, retry, confidence, and escalation contracts.
- Context providers that enforce their own authority and disclosure boundaries.

---

## 3. DSS logical functions

The DSS decomposes into the following logical functions. Each has a single purpose. They are listed in execution order, except that functions 1 and 2 run **concurrently** on this branch (§3.0, ADR-0003).

1. **Intent recognition and enrichment** converts the request into a structured capability need without binding it to a channel, resolving references against previous historical context. Enrichment may run in the same LLM call as intent recognition or as a separate step; the choice is an implementation decision. *As implemented on this branch, intent classification decomposes the turn into `asks` (each a `subject_categories` + `interaction_type` + optional `agriculture_subjects`) plus a `confidence` (§5.2), and runs independently of moderation. Enrichment is a *separate, deterministic step* rather than part of the classifier's LLM call: it resolves a scheme named colloquially ("makhana scheme", "PKVY") to the official scheme name against a tenant-mounted catalog, between classification and provider discovery (§5.2, ADR-0008).*
2. **Moderation and policy checks** decide whether an interaction may proceed. Runs in parallel with (1), judging the raw query with history as context (§3.0).
3. **Skill discovery** selects the smallest relevant set of permitted skills for the interaction.
4. **Persona and context composition** applies configured behaviour and only the context permitted for the interaction.
5. **Planning and execution** produces a plan, coordinates tool calls and Provider-capability invocations, and validates that the gathered result is sufficient to answer the need — until a sufficient result, accepted status, failure, or escalation condition is reached.
6. **Response composition and review** renders an understandable result without changing Provider-owned meaning.
7. **Channel response** shapes the reviewed content for the target channel (structured data, text, voice, media hints). Shaping only — the DSS does not deliver (§1.2).

> **Shape distinction.** Skills are injected into the prompt as reasoning guidance. Tools and Provider capabilities are exposed to the LLM as function-call schemas (name + description + input schema) that the LLM may invoke during Planning and execution.

> **Altitude disclaimer.** This is high-level design. The rationale subsections below (§3.0, §3.2) fix *boundaries and direction*, not mechanics. Ordering, seams, and responsibility splits are the decisions being recorded; thresholds, iteration bounds, retry semantics, prompt structure, and schemas are **not** settled here. Every claim below should be re-questioned against real behaviour during implementation, and this document updated when implementation contradicts it. Do not treat these subsections as specifications to code against.

### 3.0 Intent and moderation run in parallel (ADR-0003)

> **Superseded direction.** An earlier draft ran intent/enrichment **before**
> moderation so moderation could judge the *enriched* query. As implemented on this
> branch (ADR-0003) the two run **in parallel and decoupled**: moderation no longer
> consumes intent, and it judges the **raw** query. The reasoning below records the
> current decision; the follow-up-resolution concern it started from is still
> honoured, just handled differently.

A follow-up turn still cannot be judged in isolation. If turn 1 asks "what's the wheat price?" and turn 2 asks "can I grow it now?", the referent of `it` lives in history. But **resolving the reference and moderating a rewrite are separable**: rather than rewrite the query and then moderate the rewrite, moderation judges the raw query with the recent history handed to the LLM **as context**. The model resolves the reference itself; moderation never acts on words the user did not type.

This decouples the two logical functions, so they fan out concurrently (`orchestration/turn.py::run_turn`, an `asyncio.gather`) and the turn's latency is the slower of the two calls rather than their sum. Moderation still gates the result: on any non-`PROCEED` outcome the classified intent is discarded in favour of an empty `Intent()`, so a refused turn surfaces no intent read off the text it refused.

Consequences that remain load-bearing:

- **Moderation judges the raw query, with history for reference only.** `moderate()` reads `turn.original_query`; the deterministic word-check runs on that text and the LLM policies see the recent thread as reference context (`build_llm_prompt(policies, history)`). There is no enriched-query substitution step in front of moderation on this branch.
- **History is untrusted input to any prompt that reads it.** Both intent classification and moderation read unmoderated prior user text. Prior turns must be treated as data, never as instructions.

Nothing may be written to any cross-session store before moderation passes — see §5.2.

### 3.1 Bridging tools and Provider capabilities

From the LLM's point of view, MCP tools and Network-backed Provider capabilities are indistinguishable tool calls with typed inputs and outputs. The DSS translates:

- **Discovered Provider capabilities** (from `on_discover` responses obtained through the Network Consumer Adapter — see §5.4) → synthetic tool definitions (name + description + input schema) exposed to the LLM.
- **Local MCP tools** (from `list_tools` on the client MCP server) → tool definitions exposed to the LLM.

When the LLM decides to invoke:
- **Local MCP tools** — DSS calls MCP `call_tool` directly, feeds the result back.
- **Provider-backed tools** — DSS delegates to the Network Consumer Adapter, which handles Registry resolution, signing, correlation, and callback handling. DSS never speaks Network itself.

The asymmetry between a local tool and a network hop is resolved **below** the LLM: selection preference (prefer in-network, fall back to Provider) is declarative configuration, not something the model reasons about.

### 3.2 The planner / composition seam

Responsibility splits at one line: **the planner owns sufficiency, composition owns presentation.**

**Planning and execution** terminates when the gathered data is judged sufficient to answer the recognised need — not merely when the plan finishes executing. Those are different conditions: a plan can execute perfectly and still return nothing useful. Locating sufficiency here is what makes §7's "no suitable capability returns a no-match outcome rather than a fabricated answer" enforceable, because the planner is the only component positioned to know it came up empty.

**Response composition** receives data already judged sufficient and renders it — persona tone, length, `target_lang`, citations, channel shape. It does not re-open whether the data answers the query, and it does not trigger a replan.

Consequences of the split:

- Reviewer scope narrows to presentation defects, which is why the corrective retry is local to composition (§5.5).
- "A required tool was never called" is a *planning* defect caught before execution, not a runtime condition repaired downstream.
- Sufficiency is a model judgment, so the loop is bounded; on exhaustion the DSS returns the §7 no-match outcome rather than a best-effort answer built from insufficient data.

**Composition is the only stage whose output exists before its work is finished**, and this repo releases it as it is written (ADR-0011). Two consequences follow from the split above rather than from the transport:

- Sufficiency is settled before composition begins, so a piece already sent can never be invalidated by a later replan — there is no replan left to run.
- Presentation-level review therefore cannot block the stream. A reviewer that must see the whole answer before the first word leaves gives back everything streaming bought; `dss-design-v2.md` §9 already scopes review as non-blocking for this reason.

Releasing pieces is one-way: the DSS cannot recall what it has sent, so a composition failure after the first piece is reported as a failed turn rather than retried into a different answer (ADR-0011 §4).

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

**Inbound envelope.** The wire contract is `POST /v1/turns` (ADR-0006), specified in `docs/api-contracts/api-contract.md` and typed in `adapters/http/v1/schema.py`.

`adapters/http/v1/mapping.py::to_user_turn` normalizes it into the domain `UserTurn` — camelCase and provider JSON never reach the core. The last `user` message in `message.input` is the current query; earlier messages become typed `history`. `session_id` and `transaction_id` come from the request's top-level `context` object; a missing `transactionId` is minted. `transaction_id` is passed through unchanged to every `/discover` and `/select` call the turn makes, so a Provider can correlate them.

**Domain `UserTurn` (normalized shape the core works with).**

```python
class UserDetails:
    user_id: str | None = None
    phone: str | None = None
    reference: ReferenceToken | None = None   # passed on to Provider calls, not consumed

class UserTurn:
    original_query: str
    enriched_query: str       # mirrors original_query until enrichment lands
    session_id: str           # from request context.sessionId
    transaction_id: str       # from request context.transactionId; passed through to Provider calls
    source_lang: str          # language the user spoke/typed
    target_lang: str          # language the response should come back in
    channel: str              # web / voice / sms / whatsapp / ...
    user: UserDetails
    history: list[ConversationMessage]
    location: Location | None = None
    response_max_chars: int | None = None
```

**Per-component model binding (ADR-0004).** Each logical function binds its own model from the environment — `DSS_INTENT_MODEL`, `DSS_MODERATION_MODEL`, etc. — so a deployment can run intent on a small fast model and moderation on a stronger one without code change.

**Why source and target are separate.**
- `source_lang` drives inbound reasoning: Skill discovery filters skills whose `supported_languages` don't include it; Intent Recognition routes through a language-appropriate embedding model; Provider capabilities can be down-ranked if they don't serve this language.
- `target_lang` drives outbound reasoning: Persona composition may hint the LLM to draft in `target_lang`; a shipped `language_conformance` Response Reviewer verifies the candidate response is in `target_lang` and can trigger a corrective retry on drift. The retry re-renders in Response composition (§5.5) — it does not re-execute the plan.

**Translation is not a DSS responsibility.** Where translation is used, it is a conditional Experience-Layer adapter (or Provider-side capability). Any component that decrypts, translates, maps, or otherwise processes personal fields becomes an explicit personal-data processor — see §6.

**Two supported operating modes** (chosen per tenant via Identity's `reasoning_language`, if set):
- **Translate-then-reason (English-canonical DSS).** Experience Layer translates to `en` before the DSS sees it. `source_lang` on the envelope records what the user actually spoke, for routing purposes.
- **Reason-in-native (multilingual DSS).** DSS reasons in `source_lang`. Response drafted directly in `target_lang`.

**Adopter obligation on language.** Every Skill declares `supported_languages` in frontmatter; every Provider capability descriptor declares `response_languages`. Both default to `[*]` if omitted. Routing uses these to filter/rank against the envelope's `source_lang` and `target_lang`.  - Defered for now we shall come back to this later.


### 5.2 Intent-based routing

The DSS uses **intent-based routing**: extract an intent once, then match uniformly against skills, tools, and Provider capabilities.

**Intent object (as implemented on this branch — spec 0002, ADR-0003).** The classifier (`core/intent/service.py::classify_intent`) decomposes a turn into one or more **asks** plus one overall confidence:
```jsonc
{
  "asks": [
    {
      "subject_categories": "Market",        // closed enum: Crop | Livestock | Weather | Market | Scheme
      "interaction_type": "observe",         // enum: advise | observe | act
      "agriculture_subjects": "potato"       // free-text specific; null when the category needs none ("will it rain?")
    }
  ],
  "confidence": 0.88
}
```
`interaction_type` names what the farmer wants done — **advise** (explain/guide), **observe** (look up a value/record/status), **act** (book, apply, submit, update, escalate). A turn holding several needs ("wheat price and will it rain?") yields several asks. The layered-extraction cache pipeline below remains directional; this branch implements the LLM-classifier axis only, run in parallel with moderation.

**Scheme enrichment (as implemented on this branch — ADR-0008).** Between classification and discovery, `core/enrichment/service.py::resolve_scheme_subjects` rewrites a scheme ask's `agriculture_subjects` to the official scheme name, matched against a **scheme catalog** the tenant mounts as CSV (`DSS_SCHEMES_CONFIG_PATH`; nothing ships in the image). It is deterministic — a lookup, not a model call — and matches the longest whole-token **alias** span, trying the ask's own subject before the raw query.

This is a **pre-discovery hint, not a governed-code source.** Routing uses `subject_categories` alone (§5.2), so what canonicalization buys is a subject the planner can build a request from and the composer can name back to the farmer. Governed codes still come only from a provider's own advertised vocabulary via `describe_capability` (§5.4). Two consequences worth carrying forward: the catalog must hold no bare commodity words (`makhana` as an alias makes "makhana price" a scheme ask) and nothing in the code enforces that; and enrichment never *creates* an ask, so a scheme the classifier did not recognise at all stays unresolved.

**Discovering a scheme ask (ADR-0009).** Discovery resolves an ask's `(subject_categories, interaction_type)` to the `@type` values a provider can serve, using an index built from the `subjectCategories` each schema pack's examples declare. No published pack declares `Scheme`, so a scheme ask resolved to nothing and `/discover` was never called for it. A scheme ask is therefore discovered on its **subject category alone**: the `/discover` jsonpath filter already matches `subjectCategories` rather than `@type`, so the request is well-formed with no `@type` — `schemaContext` is omitted rather than sent empty. This is a named exception, not a general fallback; for every other category an unresolved pair is our own index hole and still raises `CapabilityUnresolved`.

**Domain language.** Terms used precisely throughout this document and the code:

| Term | Meaning |
|---|---|
| **Ask** | One thing a turn wants: a `subject_categories` + `interaction_type` + optional `agriculture_subjects`. A turn may hold several. |
| **Scheme catalog** | The tenant-mounted list of government schemes the deployment serves — `scheme_code`, `scheme_name`, `scheme_aliases`. Tenant-owned domain data, not operator config. |
| **Capability index** | `(subject_categories, action_type) → @type` values, inferred from the `subjectCategories` observed in each schema pack's examples. What discovery routes on; a missing pair means the DSS cannot name a type, not that no provider serves it. |
| **Alias** | One way a farmer might name a scheme ("PKVY", "organic farming scheme"). Indexed normalized; must be scheme-distinctive, never a bare commodity word. |
| **Canonicalize** | Replace an ask's free-text subject with the catalog's official scheme name, *without* changing what kind of ask it is. |
| **Governed code** | A value a provider advertises as one it serves (`supportedCommodities: 78=Tomato`). Comes from the network, never from DSS config. |

**Layered extraction (v1 direction).** Each layer is cheaper than the next; the pipeline stops at the first layer that returns a confident intent. The layers, in order:

1. **Session cache.** In-session lookup keyed by `(session_id, normalised_query)`. Hits the case where a user repeats or lightly rephrases the same question inside one conversation. Constant time, no model call, no embedding lookup. Scoped to one conversation, so it may be written inline during the turn.
2. **Global frequency cache.** Cross-session cache of the most frequently seen `normalised_query → intent` mappings for this tenant. Populated from earlier LLM-classified intents. Bounded size, LRU eviction, TTL to protect against taxonomy drift. Constant time.
3. **Pattern / regex match.** Handcrafted rules for high-confidence deterministic cases — mobile numbers, explicit slash-commands, canonical Provider names, structured queries the adopter knows about. Declared in tenant config; runs before any model is loaded. Deterministic.
4. **Embedding similarity.** Query embedding matched against artifact embeddings (built from descriptions + example queries + tagged domains). Fast, cheap.
5. **LLM classifier fallback.** If similarity confidence is low or the query is ambiguous, a small LLM call determines intent using artifact metadata. The result is written back to the global frequency cache so the next occurrence is served from layer 2.

Each layer emits its outcome (`hit` / `miss` / `low_confidence`) to operational evidence so the tenant can see the cache-hit ratio and where LLM cost is being spent.

**Cross-session cache writes happen after the answer, not during the turn.** Because intent recognition does not wait on a moderation verdict (§3.0, they run in parallel), an in-turn write to the tenant-wide layer-2 cache would let an unmoderated turn influence later turns in other sessions. The write is therefore gated on two conditions: moderation allowed the turn, **and** the turn produced a reviewed, accepted response. A turn that ended in reviewer rejection, a no-match (§7), or a Provider failure does not teach the cache that its classification was good.

Accepted trade-off: layer 2 warms more slowly, since only successful turns contribute. A cache that learns from failures is worse than a cold one.

Cache keys use the **normalised** query. Raw user utterances can carry personal data (§8.3), and a cross-session, tenant-wide store of raw utterances would be a durable record of user content — which §6.1 does not permit. Normalisation and entity stripping before key construction avoids the problem rather than mitigating it.

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

### 5.4 Catalog access — the DSS does not own the catalog

**Catalog caching lives outside the DSS.** The DSS queries capability discovery through the Network Consumer Adapter, which owns catalog caching, subscription, TTL, and invalidation. This follows directly from §1.2: the DSS does not maintain the authoritative catalog index, nor resolve Provider endpoints, keys, schemas, or participation status. An embedded, subscription-fed catalog inside the DSS would be difficult to distinguish from exactly that.

The DSS **may** cache its own selection decisions — "for this intent, this capability was chosen" — to avoid re-deriving a selection for a similar query. The constraint is what may be cached:

- **Cacheable:** capability *references* (capability + schema version), which is a record of what the DSS decided.
- **Not cacheable:** endpoints, keys, or participation status, which is a record of what is live.

Every cached reference is still resolved through the adapter at execution time, so the adapter remains free to report that a capability is unavailable or now resolves elsewhere. Provider participation changes without notice — deregistration, endpoint moves, schema bumps, serving-area changes, transient unhealthiness — and the adapter is the only component positioned to know. Caching a reference skips the selection *reasoning*; it must never skip the *resolution*.

As with intent caching (§5.2), only selections from turns that produced an accepted response are cached.

### 5.5 Plan as a first-class artifact

The planner produces an explicit **plan** — a structured, inspectable artifact — rather than deciding one step at a time and leaving the plan implicit in a tool-call trace. Three reasons, each tied to a commitment made elsewhere in this document:

- **Policy can see the whole turn.** A per-call checkpoint can only judge one call in isolation, which makes combination rules ("these two data categories must not be disclosed in the same turn") inexpressible. Evaluating a plan before execution makes them expressible.
- **Evidence comes almost free.** §6.3 requires a record of selected capability and dependency class *without* storing content. A plan is structurally that — intent-to-act with no payloads in it.
- **Adopters can verify their configuration.** A plan shows what a given config produced without anyone reading model transcripts. That is the difference between "configuration-driven" as a claim and as something an adopter can check.

**Costs accepted.** A plan is built against capability information that can shift before a later step executes; there is a planning phase before any output can stream, which matters most on voice; and a wrong plan commits to several steps of wrongness where a step-at-a-time approach would self-correct. Bounded iteration on the sufficiency check (§3.2) is what recovers the self-correction.

**Plan reuse is deferred.** A prepared-statement model — cache the plan shape for a recurring intent, bind entities per turn — is attractive, and the intent object (§5.2) is already shaped for it (`primary_domain` + `action_type` as the statement, `entities{}` as bind parameters). It is **not** a v1 commitment, because the analogy breaks in one important place: a query optimiser is deterministic and owns its schema, whereas a planner samples one plan among several possible ones. A frozen bad plan yields a confidently wrong answer, not merely a slow one. Two further hazards: a semantic analogue of parameter sniffing (a plan compiled for one region hardcoding a Provider wrong for another), and imprecise invalidation (nothing tells the DSS which cached plans referenced a capability that just changed). Policy must in any case be re-evaluated per execution — it can depend on bound entities, context, and time — so a plan cache saves the planning call, not the policy check.

v1 therefore plans fresh every turn and **instruments plan-shape recurrence**, so the decision to build a plan cache rests on a measured hit rate rather than an assumption. If built, two guardrails: cache only plans from accepted turns, and bind only entities the planner explicitly declared bindable — never inferred.

---

## 6. Personal data, security, and operational evidence

### 6.1 PII posture -- Needs more discussions.

> **The current deployment deviates from this section, deliberately.** See
> ADR-0007 and `dss-design-v2.md` §8.11, which carry the detail — this document
> is being retired, so it is not restated here.

**Shared DSS processing does not receive raw personal data.** Personal payloads required by a declared Provider capability follow the protected direct Experience-to-Provider path and are not inserted into prompts, tool registries, shared context stores, logs, traces, or analytics.

- A **Provider-scoped subject reference** — opaque to everyone except the intended Provider — may be transported by Experience and both network adapter edges without being persisted or logged by intermediaries.
- **Data-use authorization** (a compact signed artifact or a resolvable reference) states what a named Provider may do, for a declared purpose, with permitted data categories, permitted operation, validity, and revocation status. The intended Provider validates it before resolving the reference or processing protected fields.
- If a deployment enables an extension that decrypts, translates, maps, or otherwise processes personal fields, that extension is an **explicit personal-data processor**. Its purpose, data categories, retention, isolation, authorization proof, and evidence obligations must be declared separately.

### 6.2 Redaction interceptor (implementation choice)

Any DPG code path that writes to durable storage — logs, traces, telemetry payloads, metrics, session snapshots, error dumps, audit records — must route through a **redaction interceptor** before the write lands. Redaction is enforced at the **sink layer** (logger, tracer, telemetry emitter), not per component. A DSS component may log a message that references raw PII; the sink strips or pseudonymises it before persistence. This makes the rule non-bypassable by construction.

- **Baseline rules ship with DSS.** Phone / mobile numbers and Aadhaar-like patterns covered out of the box.
- **Adopter-extensible.** Adopters declare additional patterns (farmer IDs, land-record numbers, coordinates precise enough to identify a plot, jurisdiction-specific identifiers) via mounted config.
- **Redaction vs pseudonymisation.** Baseline is redaction (`phone=***`). Adopters may opt fields into pseudonymisation (`phone=usr_a1b2c3`) when stable trace-correlation across a session is needed without leaking the raw value.
- **Consequence, once built.** Persisted artifacts observable by the DPG — Langfuse traces, application logs, telemetry, on-disk error dumps — never contain raw PII. Correlation across a session is preserved through pseudonymous tokens where declared. **This is the target, not the present state**: the interceptor does not exist (§8), and the deployed tracing described in ADR-0007 writes message content to spans without it. Application logs are the one part already honoured — external request *and* response bodies go to DEBUG, so an INFO-level deployment logs the shape of a call and not its words.

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
- **No suitable capability** returns a no-match outcome rather than a fabricated answer. This also covers the case where capabilities were invoked but the result was judged insufficient to answer the need (§3.2).
- **Dependency and Provider failures** preserve the Provider status and follow the declared retry or escalation policy.
- **Low-confidence or reviewer-rejected responses** are qualified, retried, or escalated according to policy. Reviewer-triggered retries re-render the response; they do not re-execute the plan (§3.2).
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

- **Plan-then-execute** is one possible reasoning strategy — this repo uses it, with the plan as a first-class artifact (§5.5) and bounded iteration on a sufficiency check (§3.2). A single-step plan degenerates to a direct tool call and should not pay planning overhead. An implementation may use ReAct, another reasoning strategy, or a deterministic workflow while preserving the same DSS contract.
- **MCP** is one possible local-tool integration protocol — this repo uses it. An implementation may use another tool protocol.
- The logical functions may run in one process or in separately deployed modules. Deployment choices do not change the Experience Layer boundary or move Provider and Network Exchange responsibilities into the DSS.

### 8.3 Open items

DSS-scoped, deferred to v1 design and later governance:

- **Minimum DSS contracts** — request, response, tool, context, evidence, error. Now includes the **plan schema** (§5.5), since the plan is a first-class artifact that policy evaluates and evidence records.
- **Plan execution mechanics.** Step-failure handling, retry budgets, whether mid-execution replanning is ever permitted, and how partial side effects from an already-committed Provider call are reasoned about (§7 forbids changing Provider-owned state or hiding an accepted obligation). Direction: keep v1 failure behaviour simple and legible; the compensation problem is real and should not be entered accidentally.
- **Sufficiency check semantics** (§3.2) — how "enough to answer" is evaluated, its iteration bound, and the guarantee that bound exhaustion yields the §7 no-match outcome rather than a best-effort answer built from insufficient data.
- **Corrective-retry scope** (§5.5, §5.1) — confirming the retry re-renders in composition rather than re-executing the plan, and its bound.
- **Which deployment profiles require a DSS**, and which may use a deterministic Experience implementation.
- **Translation boundary and personal-data classification.** Now carries a constraint from ADR-0011: translation as a step *after* composition would remove the streaming benefit entirely for non-English farmers, since the whole English answer would have to exist before translation could begin. Translation has to stay part of generation, or streaming has to be given up for those farmers.
- **DSS-internal caches vs user context.** §5.2 and §5.4 give the DSS caches of its own decisions (intent classifications, capability references). These must stay a distinct store from the Experience-owned conversation history and profile, which the DSS reads but never writes (§1.2). Open: whether they share infrastructure, and how the boundary is enforced rather than merely intended.
- **Evaluation thresholds, confidence categories, and human-escalation requirements.**
- **Conformance tests** that prove an alternative reasoning engine or tool adapter preserves the DSS contract.
- **Exact primitive schemas.** Direction locked; concrete schemas designed with the first prototype.
- **Skill routing algorithm.** Trigger-first-then-LLM is the v1 direction; details tuned during prototyping.
- **Response Reviewers vs post-response Policies.** May unify or stay separate.
- **Router scope.** Whether Skills go through the same Router as tools/Providers.
- **Voice-channel specifics.** Concurrent moderation patterns and voice-specific latency budgets.
- **Registry of MCP tool schemas.** Currently spec/docs contracts only; promote to Schema Registry later if cross-adopter interop needs emerge.
- **Redaction interceptor implementation.** §6.2 fixes the PII posture and sink-layer enforcement model. Open: library integration vs sink processor, pseudonymisation-token wire format, adopter rule-schema shape, per-sink coverage. **Now load-bearing rather than theoretical:** ADR-0007 ships tracing that records message content with no redaction in front of it, bounded only by self-hosting and a 30-day retention window. The OpenTelemetry span processor in front of the OTLP exporter is the sink §6.2 describes, and is where this should land.
- **Stream resumability.** A dropped connection loses the pieces already sent; the DSS finishes the turn server-side and the caller recovers it from session history. ADR-0011 keeps this unchanged — no `id:` line, no `Last-Event-ID`. The known pattern is buffering each piece against `trace_id` so any replica can serve the rest on reconnect, which matters most on voice.
- **Request envelope `history` typing.** Concrete `TurnHistoryEntry` shape (roles, tool-call trace inclusion, redaction posture) deferred.
- **`UserDetails` extensibility.** Whether tenant-specific profile fields (farmer ID, region, land size) attach through an open `extra` dict on `UserDetails` or route through Context Providers projecting from a separate `user_context` payload. Leaning toward the latter.
- **PII posture — DSS envelope and forwarding rules.** The DPG architecture prescribes "shared DSS processing does not receive raw personal data" (Posture A). This repo's §5.1 envelope currently carries `user_id` and `phone`, and §6.2 implies raw PII may transit DSS with sink-layer redaction as the primary control (Posture B). Open questions: (1) which fields belong on `UserTurn` — session/interaction IDs and Provider-scoped opaque references only, or also raw identifiers? (2) does the DSS see free-text `query` when the query itself carries PII (names, addresses spoken by the user), and if so, is pre-DSS scrubbing an Experience-layer responsibility or a DSS one? (3) if PII may enter DSS in-flight, do we need per-Provider forwarding allowlists (which fields flow to which capability) in addition to sink-layer redaction? (4) how does personalisation ("Hi Ramesh…") work when DSS can't see the name — templated response with post-DSS substitution by the participating deployment, or opaque user-segment tokens? Needs discussion before v1 envelope is locked.

---

## 9. Related documents
- Model Context Protocol specification — https://modelcontextprotocol.io (implementation choice, §8.2).
