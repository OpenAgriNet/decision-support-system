# DSS design — from turn to answer

Draft 2. Rewritten after review.

---

## 1. What this is

A farmer asks a question, in their own language. The DSS works out what they need, finds who
can answer, and returns an answer with its sources. It stores nothing.

So where does this thing actually live?

---

## 2. Where it sits

**As its own container.** The Experience API(Not ready yet, Check link to get more details on expericnce layer) calls it over HTTP.

Three reasons:

**It is self contained.** A shipped library only works if their service is Python, and then
forces their Python version, their dependency resolution, and an async runtime.

**Nothing is foreclosed.** The door stays open. `core/` never imports the transport. So we can merge the two into one process later if we want.

**The two scale differently.** The DSS waits on LLM calls and is slow. Experience is I/O-bound
and fast. Scaling them together wastes replicas.

### Streaming across the boundary

One `POST`, many events back on the same open connection. Server-Sent Events.

1. Experience posts the query. The connection stays open.
2. The DSS runs the turn. Nothing is sent yet.
3. The composer starts writing claims. Each goes out as it is ready.
4. One terminal event. The connection closes.

Experience does the same to its own caller — it forwards each chunk as it arrives.

**The stream cannot be resumed today.** If the connection drops mid-turn, the DSS finishes the
turn and stores the answer. The caller re-issues with the same session id and finds it in
history.

**Two connections can drop, not one** — farmer to Experience, and Experience to DSS. The
farmer's is the fragile one: patchy mobile data, a dropped voice call.

**Resumability is open.** [Open #12]

So how does one question actually get answered?

---

## 3. The shape of a turn

```
UserTurn
   ↓
Intent Agent ──────┬──────────┬──────────┬──────────────┐
   ↓               ↓          ↓          ↓              ↓
Taxonomy       Skills      Provider    Tool         Moderation
             Discovery    Discovery  Discovery          ↓
                   ↓          ↓          ↓        Future[verdict]
                Skills   Provider     Tool              │
                         Candidates  Candidates         │
                   └──────────┴──────────┴──────────────┘
                              ↓
                       Planner Agent
                              ↓
                            Plan
                              ↓
                      Plan Executioner      ← only outside calls
                              ↓
                          Evidence
                              ↓
                     Response Composer      ← streams claims
                              ↓
                           Answer
                              ↓
                    Channel Response        ← shapes for voice/chat/sms
                              ↓
                         what the farmer gets
```

### What comes in

```python
class UserTurn:
    query: str
    history: tuple[HistoryEntry, ...]
    subject_ref: SubjectRef
    location: Location | None


class HistoryEntry:
    role: Role                   # who said it — the model needs to know
    content: str


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class SubjectRef:
    user_id: str
    ref: SecretStr | None        # never printed in logs
    issuer: str | None
    expires_at: datetime | None


class Location:
    district: str | None
    state: str | None
    lat: float | None            # rounded to 2 decimals (~1 km)
    lon: float | None
```

**Changes from draft 1:**
- `memory` **removed** — nothing reads it. Add it when something does.
- `ref` is `SecretStr` — prints as `**********`, cannot leak into a log by accident.
- lat/long - open question on rounding. [Open #8]
- `role` is an enum, not a string.

**Rules**
- `history` is untrusted.It's considered as a Data, never instructions.
- `location` is optional with no default — two of three deployments cannot fill it.
- The DSS never opens `subject_ref.ref`.

### How a turn ends

Four ways out. Not moderation-specific — several components can land on one of these.

```python
class Outcome(StrEnum):
    PROCEED = "proceed"          # answered
    REJECT = "reject"            # will not answer
    CLARIFY = "clarify"          # need more from the farmer
    NO_MATCH = "no_match"        # nothing found that can answer
```

Moderation can set `REJECT` or `CLARIFY`. A policy at any checkpoint can too. The Planner sets
`NO_MATCH` when nothing serves the ask. The orchestrator decides which path the turn took,
because only it sees the whole turn.

**Only `PROCEED` streams.** The other three send one message and stop.

Every component writes to two sinks. Not drawn.

The **TurnSink** holds the record of the turn — what was asked, what was found, what was
answered, what was refused. Required. This is the audit trail.

The **TelemetrySink** holds spans, timings and token counts. Optional. Defaults to stdout, so a
bare deployment still works. An adopter binds a tracing backend of their own.

`TurnContext` carries `trace_id` and `session_id`. Both sinks write them, so one turn can be
read across the two. The entrypoint makes the `trace_id`, and the telemetry adapter must use
that same id — if the backend makes its own, the join breaks.

Farmer content goes to the TurnSink only. Tracing backends capture prompt text by default, and
the prompt holds the query — so the adapter must mask it. [Open #11]

The record's shape is not decided yet.

One query can hold several questions:

> *"what is the price of potato? am I eligible for PM-KISAN? and when should I sow potato?"*

```python
asks = (
    Ask("potato",   "Market", LOOKUP),
    Ask("PM-KISAN", "Scheme", ADVISORY),
    Ask("potato",   "Crop",   ADVISORY),
)
```

Each ask becomes a plan step, gets executed, and feeds one answer.

There's a hard line halfway down that diagram: everything up to `Plan` can be thrown away.
Everything from `Plan Executioner` onward cannot — a Provider call may already have happened.
That barrier shapes who is allowed to call whom.

---

## 4. The orchestrator

The **entrypoint** takes the API request, builds `UserTurn` and `TurnContext`, and calls the
orchestrator once.

The **orchestrator** holds the turn and calls each component in order. Components never call
each other — every one takes plain objects and returns plain objects.

```python
class TurnContext:
    trace_id: str
    session_id: str
    source_lang: str
    target_lang: str
    channel: Channel
    tenant: str


class Channel(StrEnum):
    WEB = "web"
    WHATSAPP = "whatsapp"
    VOICE = "voice"
    SMS = "sms"
```

Illustration only — not the implementation. It shows who calls what and what is held:

```python
async def run_turn(turn: UserTurn, ctx: TurnContext) -> AsyncIterator[ChannelChunk]:
    intent = await classify(turn, taxonomy, ctx)

    skills_task                = discover_skills(turn, intent, ctx)
    provider_candidates_task   = discover_providers(intent, turn, ctx)     # all four run together
    tool_candidates_task       = discover_tools(intent, tool_index, ctx)
    moderation_verdict_task    = moderate(turn, intent, ctx)
    skills     = await skills_task
    candidates = await provider_candidates_task
    tools      = await tool_candidates_task

    plan     = await plan_turn(turn, intent, skills, candidates, tools, schemas,
                               moderation_verdict_task, ctx)
    evidence = await execute(plan, ctx)
    answer   = compose_response(evidence, plan, identity, ctx)

    async for chunk in shape(answer, ctx):
        yield chunk
```

`turn` and `ctx` stay in scope for the whole turn. That is why moderation gets `UserTurn`
without anything passing it along.

Order is code, not config. The sequence above — classify, then fan out, then plan, then
execute, then compose, then shape — is what `run_turn` does. Nothing outside this function
decides that order.

**Composition of components** We are not thinking of composing component through yaml, it will be using
python code. We can declare what all components can be optional and those components can be turned off through
config. For example **Moderation** is a mandatory component where as **Response Review** can become an optional.

**Keep it extensible.** As of now we are not able to for-see how much extensibility we should add, we want to
avoid adopter adding the code, instead they should be able to extend by configs, mcp tools, policies, hooks(this is still open).

    *Reason why we should avoid extention via code is simply because you will not be able to control what kind of code adopter will run, though we can provide some reference, test suits etc, there can still be loop holes. So we should come back to this point when we have a strong reason to allow and with proper governance and quality and security checks in place*

Before the orchestrator can run a single turn, some things have to exist already.

---

## 5. What exists before the first turn

Built once, before any turn.

| Built | From | Refreshed |
|---|---|---|
| `Policies` | DSS defaults + adopter's own | restart |
| `Taxonomy` | schema categories + adopter's subjects | interval, and/or an API call |
| `DomainSchemas` | `network-specs` packs | interval and/or API call |
| **Tool index** | each MCP server's `tools/list` | on `tools/list_changed` and/or API call |

### Policies

A policy is a rule saying what the DSS must not do. Loaded and validated at startup, then
checked at fixed points in the turn.

```python
class Policy:
    id: str                      # "acme/no-pesticide-dosage"
    checkpoint: Checkpoint       # where it is checked
    evaluation: EvaluationKind   # DETERMINISTIC or LLM
    on_violation: Outcome        # REJECT or CLARIFY. never PROCEED
    fail_mode: FailMode          # CLOSED by default
    overridable: bool


class Checkpoint(StrEnum):
    MODERATION = "moderation"
    PRE_TOOL_CALL = "pre_tool_call"
    POST_RESPONSE = "post_response"
```

`Outcome` is the same enum as **3. How a turn ends**.

**Two kinds.** A deterministic policy is a list of conditions over fields — cheap, exact, no
model. An LLM policy gives signals and examples instead, for what conditions cannot express.

**Three checkpoints, one built.** Moderation exists. Pre-tool-call and post-response are named
but not designed. See **6.2 Moderation**.

**`fail_mode` is what happens when the check itself breaks.** `CLOSED` blocks the turn — safe,
but an outage then refuses legitimate farmers. `CLOSED` is the default.

**Adopters extend, not replace.** Adopter policy ids are namespaced by tenant. `overridable:
false` cannot be turned off. `extends` adds values to a list the DSS ships rather than
replacing it.

Full schema, operators, and the loader: `0003-policy-schema.md`.

### Taxonomy

```python
class Taxonomy:
    categories: tuple[str, ...]    # must be from the schema's six
    subjects: tuple[str, ...]      # open. may be empty.
```

Categories come from the schema. Subjects come from the adopter.

**Categories are constrained.** The schema defines six(for now) — `Crop`, `Livestock`, `Weather`,
`Market`, `Scheme`, `Knowledge`. It is the `subjectCategories` enum in
`schema/AgricultureResource/v0.1/attributes.yaml`. An adopter can narrow to a subset but cannot
add any new category which is not defined in schema. A category outside the schema is a config error, caught at startup.

**Subjects are open.** The schema expects a `subjectId` from a governed taxonomy but ships no
list of them. So the adopter's list is the only source. [Open #7]

**Both can be refreshed.** The six category are not frozen — the network can add a category. When it
does, Adopter should be able to refresh local cache to get the new category -  that can be done at certain interval and/or through API call. Also once the category is added to schema, adopter should be able to update 
subject list again newly added category.

**Coarse and fine, on the same record.** Both fields are inherited from `AgricultureResource`,
so every published thing carries them. A provider that names no subjects covers its categories
broadly; naming subjects narrows it. The DSS filters the same way — category first, subject to
narrow.

Intent is the only reader. `Ask.category` must be one of `categories`; a category not in the
taxonomy is dropped. `Ask.subject` stays the farmer's own word. See **6.1 Intent**.

### DomainSchemas

Read from the network's schema files — [Dependencies #1]

[`Domain Schema`](https://github.com/OpenAgriNet/network-specs/tree/main).
The DSS reads them, never authors them. (Please check sub-branch if you don't find it in the `main`. #TODO: This should be removed)

The shape and how the planner uses it: see **6.6 Planner Agent**.

### Tool index

```python
class Tool:
    name: str                    # "soil_lookup"
    namespace: str               # "acme"
    description: str
    server_id: str
```

Call `tools/list` on each MCP server. Index each tool's name, namespace, description and
parameter names.

**Keyword search, not embeddings.** BM25. No vector store, no embedding model.

**Refrashable`.**  Listen for `tools/list_changed - A server can add or drop tools while running. Api can be added on top this for manual refresh or cron based refresh to reconciliation.

Below schema is not `confirmed` yet.
```python
class ToolIndex(Protocol):       # a port — no framework type crosses it
    def search(self, terms: Sequence[str], limit: int) -> tuple[Tool, ...]: ...
    def refresh(self, server_id: str) -> None: ...
```

The MCP client and the index live in the adapter. `core/` only sees `tuple[Tool, ...]`.

`Assumption` There will be on MCP server per local network adopter, though design will allow for more.

With that in place, here is what each component in the diagram actually does.

---

## 6. The components

### 1. Intent

A farmer asks in their own words. Intent turns that sentence into labels the rest of the
pipeline can use.

It addresses 3 things:

- **How many questions** were asked. One sentence often holds several.
- **What each is about.** A category, and the subject if named.
- **What kind of answer** is wanted. Advice, a lookup, or an action.

Intent does not answer, fetch, or pick who to call. It only labels. Everything after this reads
the labels, not the raw query.

```python
class ActionType(StrEnum):
    ADVISORY = "advisory"
    LOOKUP = "lookup"                # [Assumption #1]
    ACT = "act"


class Ask:
    subject: str | None              # "potato", "PM-KISAN". None for "will it rain"
    category: str                    # one of the taxonomy's categories
    action_type: ActionType


class Intent:
    asks: tuple[Ask, ...]
    confidence: float
```

```python
classify(turn: UserTurn, taxonomy: Taxonomy, ctx: TurnContext) -> Intent
```

**One sentence can hold several questions.** Each becomes an ask.

*"what is the price of potato? am I eligible for PM-KISAN? and when should I sow potato?"*

```python
asks = (
    Ask("potato",   "Market", LOOKUP),
    Ask("PM-KISAN", "Scheme", ADVISORY),
    Ask("potato",   "Crop",   ADVISORY),
)
```

**Rules**
- Empty `asks` means "understood nothing". A valid answer, not an error.
- A category not in the taxonomy is dropped.
- `subject` is the farmer's own word. Nothing resolves it to an id — see [Open #7].
- The ask carries no query text. The query stays whole in `UserTurn`.
- One ask becomes one plan step.
- Three asks does not mean low confidence.
- A timeout must raise. Never return a made-up Intent.

### 2. Moderation

Moderation decides whether a question should be answered at all — before anything is fetched.

One component, two parts. Harm does not read Intent.

`Outcome` is defined in **3. The shape of a turn** — Moderation is one of several components
that can set it.

```python
class ModerationVerdict:
    outcome: Outcome
    reason: ReasonCode | None
    policy_id: str | None
    refused: tuple[Refused, ...]     # what we will not answer, and why
    unsupported: tuple[ActionType, ...]


class Refused:
    what: str                        # "gold prices"
    reason: str                      # "outside agriculture"


class ReasonCode(StrEnum):
    # harm — reads the query only
    UNSAFE_ILLEGAL = "unsafe_illegal"
    ROLE_OBFUSCATION = "role_obfuscation"
    POLITICAL_CONTROVERSIAL = "political_controversial"
    EXTERNAL_REFERENCE = "external_reference"
    ADOPTER_POLICY = "adopter_policy"
    # scope — reads Intent
    DOMAIN_UNMAPPED = "domain_unmapped"
    INTENT_LOW_CONFIDENCE = "intent_low_confidence"
    UNSUPPORTED_ACTION_TYPE = "unsupported_action_type"
    # infrastructure
    UNAVAILABLE = "unavailable"
```

```python
check_harm(turn: UserTurn, ctx: TurnContext) -> HarmVerdict
check_scope(turn: UserTurn, intent: Intent, ctx: TurnContext) -> ScopeVerdict
moderate(turn, intent, ctx) -> ModerationVerdict      # runs both, one LLM call
```

**`refused` is the partial case.** "Price of potato and gold" — potato proceeds, gold is
refused. Not a rejection. The composer needs `refused` so it can say so.

**Rules**
- Harm sees the last 3 turns, wrapped in markers:
  ```
  <BEGIN CONVERSATION>
  User: ...
  Assistant: ...
  <END CONVERSATION>
  ```
  *Fake history pasted inside one message is a known attack. The markers are the defence.*

- Judges **only the current message**. History is context.
- Harm and scope share **one** LLM call.
- Fails closed. A timeout blocks the turn.
- No bot identity in the prompt.

### 3. Skills Discovery

Skills hold the know-how a tool definition cannot — how to think about a question, not what to
call. Today all of it sits in one prompt, so every turn pays for every rule. A skill is that
guidance split up, so a turn only carries what it needs.

```python
class Skill:
    id: str
    domain: str
    guidance: str                # goes into the planner prompt


class Skills:
    selected: tuple[Skill, ...]
```

```python
discover_skills(turn: UserTurn, intent: Intent, ctx: TurnContext) -> Skills
```

**Skills are read, tools are called.** A skill is guidance in a prompt — "for pest questions,
ask which crop first." It does not execute.

**Must stay local.** Picks from what this deployment already has.

**Adopters add, override, or disable.** Skills are the only primitive that can be disabled — a
tenant lists ids to switch off in `/config/skills.yaml`. A disabled skill leaves routing
entirely: no prompt tokens, no lookup, no taxonomy slot. Every shipped skill declares
`disableable` (default `true`); safety-critical ones set it `false` and config validation
rejects attempts to disable them.

**How skills get filtered is open.** 

### 4. Provider Discovery

Finds which Providers can serve an ask. It does not call them, and it does not plan.

```python
class Provider:
    id: str
    name: str
    capability: str                  # "openagrinet:MandiPriceCapability"
    kind: ProviderKind


class ProviderKind(StrEnum):
    LOCAL = "local"                  # found in this deployment's own registry
    REMOTE = "remote"                # fetched from a remote discovery service


class ProviderCandidates:
    by_ask: dict[int, tuple[Provider, ...]]     # ask index -> who can answer it
```

```python
discover(intent: Intent, turn: UserTurn, ctx: TurnContext) -> ProviderCandidates
```

**Candidates, not a choice.** The planner picks which one a step uses.

**`kind` is where the record came from, not where the Provider runs.** The network is
decentralised: this deployment has its own discovery registry, and also learns about Providers
from remote discovery services. Every Provider speaks the same schema either way.

**Runs alongside Skills and Moderation.** It needs only the intent, so it does not wait for a
plan.

**One query per ask.** Each ask in `intent.asks` gets its own query. They run at the same time.
`by_ask` is keyed by the ask's position. No asks, no queries. Filters come from the ask plus
turn context:

| Filter | From |
|---|---|
| `subjectCategories` | `Ask.category` |
| capability type | `Ask.action_type` |
| `descriptor.code` | `Ask.subject` |
| `languages` | `ctx.target_lang` |
| `coverageAreas` | `turn.location` |

**Broad or specific is adapter logic.** A Provider with no subjects listed covers its whole
category. Match those, and any that names this subject. Skip the rest.

**Every outside call goes through the network adapter** — discovery, the Beckn registry, and
Provider invocation alike. No component talks to the network itself.

**Read-only, so it may cross the barrier.** The barrier blocks calls with side effects. A
Provider call may already have happened and cannot be taken back; a discovery query can be
thrown away.

**An ask nobody can answer** gets no step. The composer says so.

### 5. Tool Discovery

Finds which MCP tools might help. Local only — no network call.

```python
class ToolCandidates:
    by_ask: dict[int, tuple[Tool, ...]]
```

```python
discover_tools(intent: Intent, index: ToolIndex, ctx: TurnContext) -> ToolCandidates
```

**Only filters past a threshold.** Ten tools or fewer: pass them all to the planner. More than
that: search. Filtering a short list costs accuracy for no gain. Anthropic's own threshold for
switching on tool search is the same — 10 tools, or 10K tokens of definitions.

```yaml
tool_discovery:
  filter_above: 10
```

**Per ask, when filtering:** narrow by namespace, then BM25 over the index. Search terms come
from the ask — subject, category, action type — plus the farmer's words for that clause.

**Providers answer the question. Tools extend what we can do.** A Provider serves the farmer's
ask; a tool is adopter capability beyond what the DPG ships. Both end up as steps, and the
executioner resolves which is which.

**Runs alongside Skills, Provider Discovery and Moderation.** Needs only the intent.

#### Picking among candidates — a strategy port

Several Providers may answer the same ask. Choosing is a port, not fixed logic.

```yaml
ports:
  provider_selector: oan_dss.adapters.selection:FirstMatch   # shipped default
```

| Strategy | What it does |
|---|---|
| `FirstMatch` | **default** — first local tool, else first remote Provider |
| `FirstMatchLocal` | first local tool only |
| `FirstMatchRemote` | first network Provider only |
| `LocalAllAndMerge` | all local tools, merged |
| `RemoteAllAndMerge` | all network Providers, merged |
| `CallAllAndMerge` | everything, merged |

Within a kind, config order decides. `on_empty` falls back to the next candidate.

**We cannot rank by quality.** Discovery tells us coverage and language, never how good a
Provider is. `generatedAt` arrives in the response, not in discovery, so freshness can only
break a tie after the calls are made. [Open #10]

### 6. Planner Agent

Discovery says who could answer. The planner decides what to actually call, in what order, and
what is still missing.

```python
class Plan:
    steps: tuple[Step, ...]
    skills: tuple[Skill, ...]              # carried through to the composer prompt
    serves: tuple[int, ...]                # index of each ask this plan covers
    refused: tuple[Refused, ...]           # carried from moderation
    missing: tuple[MissingInput, ...]      # inputs we could not fill


class MissingInput:
    name: str                        # "commodity" — the domain schema's filter name


class Step:
    id: int
    capability: str                  # what to call — a schema type, not one Provider
    inputs: dict[str, str]           # filter values. "$1.gps" = step 1's gps field
    depends_on: tuple[int, ...]      # steps that must finish first
    on_empty: Fallback | None        # what to try if this returns nothing


class Fallback:
    capability: str                  # a different capability for the same ask
    inputs: dict[str, str]
```

```python
async def plan(
    turn: UserTurn,
    intent: Intent,
    skills: Skills,
    candidates: ProviderCandidates,           # who can answer each ask
    tools: ToolCandidates,                    # MCP tools that might help
    schemas: DomainSchemas,                   # only the capabilities in candidates
    verdict: Awaitable[ModerationVerdict],    # not awaited yet
    ctx: TurnContext,
) -> Plan | Refusal:
    ...build...
    await verdict          # ← the barrier, at the last moment
```

**Only the schemas this turn needs.** The cache holds every schema. The planner gets the ones
for capabilities in `candidates`, nothing more — the rest would be prompt tokens for
capabilities it cannot call. How that filtering works is not decided. [Open #13]

#### DomainSchemas — the network schema input

```python
class DomainSchema:
    type: str                        # "openagrinet:MandiPriceCapability"
    filterable: tuple[str, ...]      # from profile.json filterable_paths
    required: tuple[str, ...]        # filters that must be supplied


class DomainSchemas:
    by_type: dict[str, DomainSchema]
```

**The DSS reads domain schemas. It never authors them.** Schema management lives outside the
DSS, in `network-specs`. Read-only dependency behind a port, same as taxonomy.

**Cached and refreshed on an interval**, like taxonomy. Network-sourced, so dynamically
resolvable per D1. A stale cache serves the last good copy rather than failing the turn.

**Rules**
- The plan is **data**, not code. The runtime reads it. No `eval`.
- Must serve every ask in `intent.asks`, or report why not.
- One step, one capability. Three tools means three steps.
- Validated before running: does the capability exist, are inputs filled, is the graph acyclic.

**`missing` and `steps` are independent (D21).** There is no separate `Clarification` type. A
plan may carry steps, missing inputs, or both:

| `steps` | `missing` | What happens |
|---|---|---|
| some | empty | run it, answer |
| some | some | **run what we can, ask about the rest** — the farmer gets something |
| empty | some | pure clarification — `NEEDS_CLARIFICATION` |
| empty | empty | nothing to do — `NO_MATCH` |

- The **planner** detects a missing input, not intent. Intent does not know which capability
  will be chosen, and two providers in the same domain may need different inputs. The planner
  reads the capability's input schema and finds the hole.
- `missing` means ask the farmer, do not guess.
- A step whose input is missing is **not** emitted. Steps that can run, run.

**`name` is the domain schema's filter name** — `"commodity"`. The schema is shared across every
Provider serving that domain, so there is one vocabulary and no per-Provider mapping.

The composer writes the question, in `ctx.target_lang`. The planner does not write user-facing
text.

*Later if needed:* `options` (a short value list, once something can fill it) and a render hint
for non-text input. Both are fields with defaults — cheap to add.

### 7. Plan Executioner

The plan says what to call. The executioner is the only thing that actually calls it, and the
only thing that touches the outside world.

```python
class Source:
    id: str                      # "1", "2" — what a claim cites
    name: str                    # "Agmarknet"
    kind: str                    # "provider" | "document" | "tool"
    url: str | None


class Evidence:
    sources: tuple[Source, ...]      # numbered, for citation
    results: tuple[Result, ...]
    served: tuple[int, ...]          # index of each ask actually answered
    failed: tuple[Failure, ...]
    sufficient: bool


class Result:
    step_id: int
    source_id: str
    data: dict                       # mapped to the domain schema


class Failure:
    step_id: int
    capability: str
    reason: str
    retryable: bool
```

```python
execute(plan: Plan, ctx: TurnContext) -> Evidence
```

**Rules**
- The only component that touches the outside world.
- No LLM. It reads the plan and calls things. Every branch it can take — `depends_on`,
  `inputs`, `on_empty` — is already a field in the plan.
- MCP tools and provider capabilities both go through here. The runtime resolves which.
- `sufficient=False` means the answer is "I don't know". Never invent.
- MCP calls are cancellable. Provider calls are not — they may already have happened.

### 8. Response Composer

```python
class Answer:
    claims: tuple[Claim, ...]
    sources: tuple[Source, ...]
    confidence: Confidence
    refused: tuple[Refused, ...]
    limitations: tuple[str, ...]


class Claim:
    text: str                        # one sentence
    source_id: str | None            # None for connective sentences


class Confidence(StrEnum):
    HIGH = "high"
    LOW = "low"
```

```python
compose(evidence: Evidence, plan: Plan, identity: Identity, ctx: TurnContext) -> AsyncIterator[Claim]


class Identity:
    name: str                        # "Kisan Mitra"
    persona: str
    boundaries: str
```

**Rules**
- Streams **claims**, not characters. Each claim is complete when emitted.
- Cites where it can. Uncited connective sentences are fine — loose for now.
- Says what it refused, from `refused`.
- Says "I don't know" when `sufficient=False`.
- Keeps the Provider's meaning. Rewording fine, changing facts not.
- Identity goes here and nowhere else.

### 9. Response Reviewer — optional port

```python
class ReviewVerdict:
    grounded: bool
    violations: tuple[Violation, ...]


class Violation:
    claim_index: int
    kind: str                        # "ungrounded" | "unsafe" | "too_long" | "tone"
    detail: str
```

```python
review(answer: Answer, evidence: Evidence, ctx: TurnContext) -> ReviewVerdict
```

**Rules**
- **Does not block the stream.** Logs, flags for eval, may append a correction.
- Checks each claim against **its own cited source**, not the whole answer against everything.
- Checks grounding, safety, length, tone. Language checks deferred.
- Optional. When unbound, log at startup: *"Response review disabled — grounding violations
  will not be detected."*
  
*We need to revisit this*

### 10. Channel Response — what it gets

**This is the answer to the question.** The channel component receives:

```python
shape(answer: Answer, ctx: TurnContext) -> AsyncIterator[ChannelChunk]
```

**In:** the `Answer` — claims with source ids, the source list, confidence, what was refused.
Plus `ctx.channel` and `ctx.target_lang`.

**Not in:** the plan, the evidence, the raw results, the moderation verdict. It shapes what
was written; it does not decide what to say.

**Out:**

```python
class ChannelChunk:
    text: str                        # ready to send, already shaped
    is_final: bool


class TerminalEvent:
    status: Status
    cause: str | None
    text: str                        # the full answer
    sources: tuple[Source, ...]
    confidence: Confidence
    trace_id: str
    limitations: tuple[str, ...]
    refused: tuple[Refused, ...]


class Status(StrEnum):
    ANSWERED = "answered"
    REJECTED = "rejected"
    NO_MATCH = "no_match"
    NEEDS_CLARIFICATION = "needs_clarification"
    ERROR = "error"
```

**What it does per channel:**

| Channel | Chunk size | Citations | Sources |
|---|---|---|---|
| Voice | one speakable phrase | stripped | spoken at the end, or dropped |
| Web / WhatsApp | ~300 chars | inline `[1]` | listed at the bottom |
| SMS | whole message, no streaming | dropped | dropped, or one link |

Same `Answer` in, four different outputs. The composer never learns which channel it served.

That's each piece on its own. Here is all eleven working on one real question.

---

## 7. One real question, end to end

> *"I am planning to sow potato, can you tell me which soil is best? Also, what is the price of
> potato and gold?"*

Three asks. Two agriculture, one not.

### 1. UserTurn — in

```python
UserTurn(
    query="I am planning to sow potato, can you tell me which soil is best? "
          "Also, what is the price of potato and gold?",
    history=(),
    subject_ref=SubjectRef(user_id="u-8821", ref=SecretStr("****"), ...),
    location=Location(district="Anand", state="Gujarat", lat=22.56, lon=72.93),
)

TurnContext(trace_id="t-4f2a", session_id="s-19", source_lang="en",
            target_lang="en", channel=Channel.WHATSAPP, tenant="gj")
```

### 2. Intent

**In:** `UserTurn`, `Taxonomy`, `ctx`

```python
Intent(
    asks=(
        Ask(subject="potato", category="Crop",   action_type=ActionType.ADVISORY),
        Ask(subject="potato", category="Market", action_type=ActionType.LOOKUP),
    ),
    confidence=0.91,
)
```

Gold has no category in the taxonomy, so no ask is made for it. Intent does not refuse — that is
moderation.

### 3. Moderation

**In:** `UserTurn`, `Intent`, `ctx` — one LLM call, harm + scope

```python
ModerationVerdict(
    outcome=Outcome.PROCEED,
    reason=None,
    policy_id=None,
    refused=(Refused(what="gold prices", reason="outside agriculture"),),
    unsupported=(),
)
```

**The partial case.** Not a rejection — potato proceeds, gold is refused and carried forward so
the composer can say so.

### 4. Skills Discovery

**In:** `UserTurn`, `Intent`, `ctx`

```python
Skills(selected=(
    Skill(id="soil-advisory", domain="Crop",
          guidance="For sowing questions, state soil type, pH range and drainage."),
))
```

Local only. No network call before the barrier.

### 5. Provider Discovery

**In:** `Intent`, `UserTurn`, `ctx` — one query per ask, run together

```python
ProviderCandidates(by_ask={
    0: (Provider(id="krishi-kb", name="Krishi Knowledge Base",
                 capability="openagrinet:KnowledgeRetrievalCapability", kind=LOCAL),),
    1: (Provider(id="agmarknet", name="Agmarknet",
                 capability="openagrinet:MandiPriceCapability", kind=REMOTE),),
})
```

Ask 2 is gold, which produced no ask, so there is nothing to look up.

### 6. Tool Discovery

**In:** `Intent`, `ToolIndex`, `ctx` — local, no network call

Under 10 tools in this deployment, so no filtering. All of them go through.

```python
ToolCandidates(tools=(
    Tool(name="soil_lookup", namespace="acme",
         description="Soil type and pH by district", server_id="acme-mcp"),
))
```

### 7. Planner

**In:** `UserTurn`, `Intent`, `Skills`, `ProviderCandidates`, `ToolCandidates`,
`DomainSchemas`, `Awaitable[verdict]`, `ctx`

The schemas it reads:

```python
DomainSchema(type="openagrinet:KnowledgeRetrievalCapability",
             filterable=("subjectCategories", "agricultureSubjects.subjectId", "languages"),
             required=("subjectCategories",))

DomainSchema(type="openagrinet:MandiPriceCapability",
             filterable=("commodity.code", "market.marketCode", "market.state", "arrivalDate"),
             required=("commodity.code", "market.state"))
```

One step per ask. Ask 0 has everything. Ask 1 has `commodity.code="potato"` from the query and
`market.state="Gujarat"` from `location`. Both fillable.

```python
Plan(
    steps=(
        Step(id=1, capability="openagrinet:KnowledgeRetrievalCapability",
             inputs={"subjectCategories": "Crop",
                     "agricultureSubjects.descriptor.code": "potato"},
             depends_on=(), on_empty=None),
        Step(id=2, capability="openagrinet:MandiPriceCapability",
             inputs={"commodity.code": "potato", "market.state": "Gujarat"},
             depends_on=(), on_empty=None),
    ),
    skills=(Skill(id="soil-advisory", ...),),
    serves=(0, 1),
    refused=(Refused(what="gold prices", reason="outside agriculture"),),
    missing=(),
)
```

Filtering is on `descriptor.code`, not `subjectId` — there is no way to resolve "potato" to a
governed id. See [Open #7].

Two independent steps — the executor runs them in parallel. `await verdict` happens last.

**The `missing` variant:** had `location` been `None`, `market.state` is unfillable → step 2 is
not emitted, and `missing=(MissingInput(name="market.state"),)`. Step 1 still runs. The farmer
gets the soil answer plus "which district?".

### 8. Plan Executioner

**In:** `Plan`, `ctx`. The only component that touches the outside world. No LLM.

```python
Evidence(
    sources=(Source(id="1", name="ICAR Potato Guide", kind="document", url="…"),
             Source(id="2", name="Agmarknet", kind="provider", url=None)),
    results=(
        Result(step_id=1, source_id="1",
               data={"soilType": "sandy loam", "phRange": "5.2–6.4",
                     "drainage": "well-drained"}),
        Result(step_id=2, source_id="2",
               data={"commodity": "Potato", "market": {"marketName": "Anand"},
                     "prices": {"modal": 1450, "unit": "INR/quintal"},
                     "arrivalDate": "2026-08-25"}),
    ),
    served=(0, 1),
    failed=(),
    sufficient=True,
)
```

### 9. Response Composer

**In:** `Evidence`, `Plan`, `Identity`, `ctx` — streams claims

```python
Answer(
    claims=(
        Claim(text="Potato grows best in well-drained sandy loam soil.", source_id="1"),
        Claim(text="Keep soil pH between 5.2 and 6.4.", source_id="1"),
        Claim(text="Potato is ₹1,450 per quintal at Anand mandi today.", source_id="2"),
        Claim(text="I cannot help with gold prices.", source_id=None),
    ),
    sources=(Source(id="1", ...), Source(id="2", ...)),
    confidence=Confidence.HIGH,
    refused=(Refused(what="gold prices", reason="outside agriculture"),),
    limitations=(),
)
```

### 10. Response Reviewer — optional

**In:** `Answer`, `Evidence`, `ctx` → `ReviewVerdict(grounded=True, violations=())`

Does not block the stream. Logs only.

### 11. Channel Response

**In:** `Answer` + `ctx.channel` + `ctx.target_lang`. Not the plan, not the evidence.

WhatsApp — inline markers and a source list:

```
Potato grows best in well-drained sandy loam soil. [1]
Keep soil pH between 5.2 and 6.4. [1]
Potato is ₹1,450 per quintal at Anand mandi today. [2]
I cannot help with gold prices.

[1] ICAR Potato Guide  [2] Agmarknet
```

Same `Answer` on voice: markers stripped, claims grouped into speakable phrases. On SMS:
trimmed, no markers.

---

## 8. What is still open

1. Current struture for policy might not work for Response, need to check on this later.
2. **Regeneration limit** — if review can trigger a rewrite, how many before giving up?
3. **Required filters** — `network-specs` marks what you *may* filter on, not what you *must* send. `DomainSchema.required` has no source yet.
4. **Operator-validity table** — 0003 refers to one, never prints it.
5. **Language validation** — Gujarati script correctness, deferred. English only for now.
6. **Does a conversation need a summary, and who keeps it?** Keeping it in memory means it is
   lost when a replica restarts. Keeping it in a store means the DSS holds farmer content.
   Experience could keep it instead — or we could do without.
7. **Subjects list for taxonomy.** We can identify a category from the schema, but there is no
   list of `subjects` across networks. We would need one, or semantic search on the discovery
   service.
8. **Rounding lat/long.** Should it be rounded so a farmer is not identified by exact location,
   and if so to what range? This sits with the Experience layer — the DSS receives what it is
   given.
9. **Nothing serves `ACT` yet.** Every schema type is advisory, observation, or resource. No
   Provider type books or submits anything. So an `ACT` ask finds no Provider and gets no step —
   the normal no-match path, but worth knowing it is the only outcome today.
10. **Which Provider do we call, and how do we rank answers?** Several can serve one ask, and
    discovery tells us nothing about quality — only coverage, language and capability type. We
    learn how fresh an answer is after the call, not before. And if two Providers report
    different prices, nothing decides which is right.
11. **What may telemetry hold, and for how long?** Farmer content reaches the sinks. Open: the
    retention window, masking before write, who may query it, and whether reads are audited.
12. **Can a dropped stream be resumed?** Not today. The known pattern is to buffer each claim
    against `trace_id` as it is emitted, so generation finishes whether anyone is listening and
    any replica can serve the rest on reconnect. Matters most on voice, where a dropped call
    means the whole answer is spoken again.
13. **How do we filter schemas down to what a turn needs?** The planner should get only the
    schemas for capabilities in `candidates`, not the whole cache. Where that filtering happens,
    and what it keys on, is undecided.
14. **Who decides `sufficient`?** It is a judgement, and the executioner has no LLM. The
    architecture doc puts sufficiency with the planner, but the field sits on `Evidence`. To be
    settled during implementation.

## 9. Dependencies 
1. As of now we are considering to read it from GitHub Repo, but if there are some change in the schema location we would need to change as well.

## 10. Assumption

1. We are using Lookup instead of Observe as there was comment if we can use some other word instead of Observ,
we will update once we settle on name.