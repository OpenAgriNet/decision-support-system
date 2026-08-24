# 0003 — Policy schema, default pack, and loader

**Issue:** #57 · **Status:** spec under review · **PR B of three**

Depends on [`0002-intent-contract.md`](./0002-intent-contract.md) — policies validate
their field paths against `Intent` and `UserTurn`, so those types must exist first.

## Context

Three sibling production repos (`bharat-oan-api`, `mh-oan-api`, `amul-oan-api`)
already run moderation, and their policy handling has three problems this change
exists to fix.

**Policy is hardcoded prose.** All three render
`assets/prompts/moderation_system.md` through Jinja2 with an **empty** context — no
variables, no per-tenant override. The category list is duplicated between a Python
`Literal[...]` and the markdown, so adding one is a two-place edit.

**The three policies genuinely conflict**, and nothing reconciles them:

| difference | bharat | mh | amul |
|---|---|---|---|
| in-doubt bias | "decline rather than allow" | "be generous" | "downstream decides" |
| protest/advocacy | agricultural | **non**-agricultural | — |
| language allowlist | non-Indian only | English + Marathi | English + Gujarati |
| categories | 8 | 9 | 9 |

**Two of the three don't enforce the verdict at all.**
`bharat-oan-api/app/services/chat.py:466` has no branch on the result — it injects
the verdict into the agent prompt as prose and trusts the model, so an
`unsafe_illegal` verdict still reaches the tool-calling agent. That's 0004's problem
to fix, but it's why the policy contract has to carry an enforceable action rather
than advisory text.

Outcome of this change: one declarative, startup-validated policy pack that expresses
those differences as **data**, with safety policies an adopter can strengthen but
never weaken.

---

## Scope

**In:** policy models, the 6-policy default pack, the published JSON Schema, the
loader with merge/precedence/validation, and the `validate-config` subcommand.

**Out:** evaluation. Nothing in this change *runs* a policy — that's 0004. This PR
produces validated configuration and the types it deserializes into.

---

## The contract

### `src/dss/core/policy/models.py`

A flat module, not a package. Three enums plus three model types doesn't earn a
directory.

```python
class Checkpoint(StrEnum):
    MODERATION = "moderation"          # implemented
    PRE_TOOL_CALL = "pre_tool_call"    # declared and validated, not evaluated
    POST_RESPONSE = "post_response"


class EvaluationKind(StrEnum):
    DETERMINISTIC = "deterministic"    # plain Python over the context
    LLM = "llm"                        # batched into one judgment call


class FailMode(StrEnum):
    CLOSED = "closed"                  # on error, reject the turn
    OPEN = "open"                      # on error, let the turn proceed
```

**A `Checkpoint` enum from day one, even with two members unused.** It makes the
registry and the loader's grouping typed, and an unknown `checkpoint:` in YAML
becomes an ordinary Pydantic error with no custom check needed.

#### What `fail_mode` decides

The moderation call times out. Now what?

- **`closed`** (default) — reject the turn. Nothing unmoderated proceeds. The cost is
  real: a farmer with a legitimate question is refused because of *our* infrastructure
  problem.
- **`open`** — let it through unmoderated.

`open` looks indefensible until there's a side-effecting operation. The case comes
from amul's code, commented *"a flaky moderation check must never drop a real farmer
booking"* — losing a real transaction is concrete harm, where an under-moderated
advisory answer is diffuse. Different calculus, so it's per-policy rather than one
global switch.

Every policy in the shipped pack is `closed`. There are no tools yet; the field
exists so the exception is expressible when they arrive, and so the default is a
recorded decision rather than an accident.

### `Condition` — one predicate over one field

```python
class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str                                   # e.g. "intent.action_types"
    is_: Literal["empty", "present"] | None = Field(default=None, alias="is")
    in_: list[str] | None = Field(default=None, alias="in")
    not_in: list[str] | None = None
    contains: str | None = None
    equals: str | int | float | bool | None = None
    lt: float | None = None
    lte: float | None = None
    gt: float | None = None
    gte: float | None = None

    @model_validator(mode="after")
    def exactly_one_operator(self) -> Condition: ...
```

`is` and `in` are Python keywords, so the attributes are `is_`/`in_` with an alias
keeping the YAML as `is:`/`in:`. This is the one place the "no transformation at the
boundary" convention bends, and it bends for a language constraint rather than taste.

#### Why `contains` is a separate operator

Not because of list-vs-scalar. Because the two operators put the collection on
**opposite sides**:

- `in` / `not_in` — the **operand** is the collection, the field value is the element.
  `{field: turn.channel, in: [web, whatsapp]}`
- `contains` — the **field** is the collection, the operand is the element.
  `{field: intent.domains, contains: dairy}`

Genuinely different relations. Collapsing them and disambiguating by field type would
make `{field: X, in: [...]}` mean *intersection* when X is a list and *membership*
when X is a scalar — so a reader would need to know X's Python type to know what the
policy says. Config whose meaning depends on a type invisible in the file is a
support burden.

`contains` also works on `str` as substring match, which is the same "field is the
container" relation.

#### List semantics, defined once

`intent.domains` and `intent.action_types` are lists, so list behaviour is settled
here rather than guessed per policy:

| check | on a list | plain English |
|---|---|---|
| `is: empty` | length 0 | we recognised nothing |
| `is: present` | length 1+ | we recognised something |
| `contains` | membership | at least one matches |
| `not_in` | disjoint | not a single one matches |

`not_in` as *"none of them match"* is the cautious reading, deliberately: if a query
touches something unsupported, we'd rather notice than quietly serve half of it.

**Open question for review:** on a list field, should `in: [a, b]` mean *intersects*
or *is a subset of*? Both defensible. If it can't be settled confidently, ship
without `in` for collection fields and add it when a real policy needs it — that's
the cheaper mistake to unwind.

### `Policy` — a discriminated union, not one class with a validator

A deterministic policy is driven by `when`; an LLM policy by `signals`/`examples`. An
earlier draft put all four fields on one class with a validator rejecting the wrong
combinations — which is a class admitting it's two types: half the fields are always
empty, and which ones are live depends on a sibling field's value.

```python
class PolicyBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str                              # kebab-case; "<tenant>/<id>" if adopter
    checkpoint: Checkpoint
    on_violation: Outcome
    description: str

    overridable: bool = True
    fail_mode: FailMode = FailMode.CLOSED
    extends: dict[str, list[str]] = Field(default_factory=dict)


class DeterministicPolicy(PolicyBase):
    evaluation: Literal[EvaluationKind.DETERMINISTIC]
    when: list[Condition] = Field(min_length=1)          # implicit AND


class LlmPolicy(PolicyBase):
    evaluation: Literal[EvaluationKind.LLM]
    signals: list[str] = Field(min_length=1)
    examples: list[PolicyExample] = Field(default_factory=list)


Policy = Annotated[
    DeterministicPolicy | LlmPolicy,
    Field(discriminator="evaluation"),
]


class PolicyExample(BaseModel):
    """A worked case. Feeds the LLM prompt and doubles as a test fixture."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str
    expect: Outcome
```

`min_length=1` does declaratively what the validator did: a deterministic policy with
no conditions can never fire, and an LLM policy with no signals has nothing to judge
on. Both are now unconstructable rather than caught late.

### What "adopter policies are namespaced" means

Plainly: **DSS policy names have no prefix; adopter policy names must have one.**

```yaml
unsafe-illegal              # a DSS name — reserved
amul/local-banned-pesticides   # an adopter name — the "amul/" is required
```

Without the rule, an adopter could write a policy called `unsafe-illegal`, and
because names are the override key, theirs would replace the DSS safety policy. The
prefix makes that impossible: a bare name in an adopter file is a startup error.

### What `overridable: false` means

Two parts:

**The action is locked.** `unsafe-illegal` says `on_violation: reject`, and an adopter
cannot change that to `allow`.

**Guidance can only be added to, never removed.** `extends:` appends. So Amul can add
a pesticide banned locally in Gujarat:

```yaml
- id: unsafe-illegal
  extends:
    banned_substances: [monocrotophos]     # added to what DSS ships
```

but cannot hand back a shorter list to delete DSS entries.

The asymmetry is the point: **making a safety policy stricter is easy; making it
looser is impossible.**

Residual risk, accepted honestly: a semantic loophole added as an extension —
`extends: signals: ["unless the farmer has a licence"]` — is append-only, validates
cleanly, and guts the policy. Config needs review like code does; no schema closes
that.

### `PolicyPack`

```python
class PolicyPack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    policies: list[Policy]

    @model_validator(mode="after")
    def ids_unique(self) -> PolicyPack: ...
```

### `reason_code` is derived, not declared

An earlier draft had it as a field, so the same fact was stated three times: the
policy id (`unsafe-illegal`), the `reason_code` (`unsafe_illegal`), and the enum
member. Two of the three can drift — nothing would object to:

```yaml
- id: unsafe-illegal
  reason_code: political_controversial     # ← contradicts its own id
```

So the id is the single source of truth and `reason_code` is computed from it.
Validation asserts every DSS policy id maps to a known `ReasonCode`, which also stops
a policy being renamed without the enum following.

`ReasonCode` itself is defined in 0004, alongside the decision type that carries it.

---

## Checkpoint dispatch — build no abstraction

An earlier draft had the config loader grouping policies by checkpoint and handing
them to the moderation service. The instinct that this is wrong was right, but the
diagnosis was off: an *index over loaded config* is squarely a loader's job, the same
way a skills loader will want `skills_by_domain`. The actual smell was the loader
**handing** policies to a service — the loader knowing which service exists.

So invert it:

```
config/         load_policies() → validate → policies_for(checkpoint)
                  depends on nothing above it

core/policy/    the evaluator, taking an explicit context object
                  never mentions "turn" or "intent" by name

orchestration/  pulls policies_for(MODERATION), builds the context,
                  calls the evaluator, acts on the verdict
```

**Why not a `PolicyCheckpoint` class per checkpoint.** It conflates two things that
vary independently:

- **input assembly** — genuinely different per checkpoint (moderation has 2 roots,
  pre-tool-call has 4), and belongs to orchestration since only orchestration knows
  when a `Plan` exists
- **evaluation** — *identical* across all three. A `{field, lt}` predicate against a
  resolved value does not care which checkpoint produced it

Write a class per checkpoint and the evaluation half gets duplicated three times or
extracted into a helper — which is the shared evaluator you'd have had by not writing
the classes.

**Cost, stated honestly:** orchestration gets a few lines of glue per checkpoint —
fetch group, build context, evaluate, act. Three checkpoints means it appears three
times. That's the right cost: `AGENTS.md` says orchestration's job is "defines control
flow and policy checkpoints", and deduplicating five lines of wiring by introducing a
protocol is a bad trade. It also means `on_violation` handling — which is genuinely
*not* uniform across checkpoints (block a turn vs. drop a tool call vs. regenerate a
response) — lands as three explicit handlers rather than a leaky enum in a shared
abstraction.

**How checkpoint #2 arrives without rewriting #1:** define its context model, add one
registry line, add glue in orchestration. Zero lines of the moderation path and zero
lines of the evaluator change — provided the evaluator never names a root. Enforce
that with a tier-1 test that evaluates a policy against a throwaway two-root context
model defined *in the test file*. If that passes, the evaluator is provably
checkpoint-agnostic without any checkpoint abstraction existing.

---

## Field paths — a context model per checkpoint

The roots in scope differ per checkpoint:

| checkpoint | roots | status |
|---|---|---|
| `moderation` | `turn.*`, `intent.*` | implemented |
| `pre_tool_call` | `turn.*`, `intent.*`, `plan.*`, `tool.*` | declared, not evaluated |
| `post_response` | `turn.*`, `intent.*`, `response.*` | declared, not evaluated |

**The design: a Pydantic model per checkpoint whose fields *are* the roots.**

```python
class ModerationContext(BaseModel):
    turn: UserTurn
    intent: Intent


CHECKPOINT_CONTEXTS: dict[Checkpoint, type[BaseModel]] = {
    Checkpoint.MODERATION: ModerationContext,
}
```

This gives both requirements from one structure:

**Startup validation** walks `model_fields` segment by segment. `FieldInfo.annotation`
is the resolved type, so this was verified working against the repo's actual
interpreter:

```
intent.confidence   → <class 'float'>
turn.channel        → Literal['web', 'whatsapp', 'voice']
intent.confidance   → ERR unknown segment 'confidance'; available: ['confidence', 'domains']
tool.requires_auth  → ERR unknown segment 'tool'; available: ['intent', 'turn']
```

Two things fall out free. The typo case reports available siblings — feed that to
`difflib.get_close_matches` and the boot error becomes *"unknown field
`intent.confidance`; did you mean `intent.confidence`?"*. And **root scoping needs no
separate mechanism**: an out-of-scope root fails on the *first* segment, because
there is no field for it. `tool.*` at the moderation checkpoint is not rejected by a
rule — it is unexpressible.

**Runtime resolution** is `functools.reduce(getattr, path.split("."), ctx)`. One line,
no special cases, and guaranteed not to raise in production *because* startup
validation already proved every segment exists on the type.

That guarantee is the payoff for validating against the same structure you resolve
against. The alternative — a dict of root-name → type for validation plus a parallel
dict of root-name → instance at runtime — gives two structures that can drift, which
is the bug you debug in six months.

Use `model_fields` (on the class, not the instance — Pydantic 2.11+ deprecates the
latter) rather than `typing.get_type_hints`: it's the pre-resolved, field-only view,
so strictly less code for strictly more correctness.

Adding a checkpoint is one context model plus one registry line. Adding a root to an
existing checkpoint is one field. Both single-site.

**One discipline to hold:** the context model must stay a pure input bundle. If a
field on it isn't addressable by a policy `field` path, it doesn't belong there. It
must not become the pipeline's god-object.

---

## Startup validation

Fails hard with actionable errors, following the best precedent in the siblings
(`bharat-oan-api/agents/models.py:143-163` — collect all problems, raise once, name
the UPPER_CASE env var rather than the Python attribute). An adopter fixing a
fifteen-policy pack should not need fifteen boot cycles.

Each check below prevents a policy that **looks configured and does nothing** — the
worst failure mode, because it passes review and never fires.

**1. A field that doesn't exist**
```yaml
when: [{field: intent.primary_domian, is: empty}]   # 'domian'
```

**2. A field not in scope at this checkpoint**
```yaml
checkpoint: moderation
when: [{field: tool.requires_auth, is: true}]       # no tool here yet
```

**3. An operator that doesn't fit the field's type**
```yaml
when: [{field: intent.confidence, contains: x}]     # contains on a float
```
Verified working: the annotation is classified into a capability class — `ordered`
(int/float), `text` (str), `scalar_enum` (Literal/StrEnum), `bool`, `collection`
(list/set/tuple) — then the operator is looked up in a table.

`str | None` classifies as `text`, since the classifier unwraps optionality via
`get_origin`/`get_args` and recurses. Get that right early; it's the case that bites.

`dict` and nested-model fields are **rejected as policy targets in v1**.
`intent.entities` is a dict, and `lt` on a dict has no sensible meaning. Failing at
boot with *"field `intent.entities` is a mapping; address a concrete key instead"* is
much better than silence — and it forces the question of whether
`intent.entities.mobile` should be addressable to be answered on purpose.

**4. An operand that doesn't fit the field's type**
```yaml
when: [{field: turn.channel, equals: sms}]          # not a channel
```
This passes check 3 (`equals` is valid on an enum) and is still wrong. `TypeAdapter`
catches it — verified: `equals: 'sms'` → *"Input should be 'web', 'whatsapp' or
'voice'"*. Which type to validate against follows from the `contains`/`in` split:

| operator | operand validated as |
|---|---|
| `equals`, `lt`/`lte`/`gt`/`gte` | the field type — `TypeAdapter(T)` |
| `in` / `not_in` | a list of it — `TypeAdapter(list[T])` |
| `contains` on a collection | the element type — `get_args(T)[0]` |
| `contains` on `str` | `str` |
| `is` | not data — `Literal["empty","present"]` |

Build these adapters once at startup and keep them on the compiled predicate:
construction is the expensive part, so this is validation and a small runtime win
together.

**5. An adopter using a reserved (bare) name**
```yaml
- id: unsafe-illegal        # must be amul/something
```

**6. Overriding a locked action**
```yaml
- id: unsafe-illegal
  on_violation: allow       # overridable: false
```

**7. Deleting DSS-shipped guidance**
```yaml
- id: unsafe-illegal
  extends: {banned_substances: []}     # replacing, not appending
```

**8. A DSS policy id with no matching reason code**
```yaml
- id: unsafe-illegall       # doubled 'l' → nothing to report in evidence
```

---

## Default pack — `src/dss/config/defaults/policies.yaml`

Six policies:

| id | evaluation | overridable | outcome |
|---|---|---|---|
| `require-mapped-domain` | deterministic | true | reject |
| `low-confidence-intent` | deterministic | true | clarify |
| `unsafe-illegal` | llm | **false** | reject |
| `role-obfuscation` | llm | **false** | reject |
| `political-controversial` | llm | true | reject |
| `external-reference` | llm | true | reject |

Two complete examples, one of each kind:

```yaml
- id: low-confidence-intent
  checkpoint: moderation
  evaluation: deterministic
  on_violation: clarify
  overridable: true
  description: >
    A domain was recognised but weakly. Ask rather than guess — a farmer whose
    phrasing confused the classifier deserves a second chance, not a refusal.
  when:
    - field: intent.domains
      is: present
    - field: intent.confidence
      lt: 0.5
```

```yaml
- id: unsafe-illegal
  checkpoint: moderation
  evaluation: llm
  on_violation: reject
  overridable: false
  fail_mode: closed
  description: >
    Requests that would cause harm or facilitate illegality, including when
    wrapped in legitimate farming language.
  signals:
    - banned or restricted agrochemicals, including when framed as a question
    - insurance fraud, scheme gaming, falsified evidence, dual registration
  extends:
    banned_substances: []
  examples:
    - query: Kya Aluminum Phosphide se chuhe bhi mar sakte hain aur kuch aur bhi?
      expect: reject
```

`examples` do double duty: they render into the moderation prompt *and* serve as test
fixtures, so a policy ships with the cases proving it works.

### Why there is no `advisory-only` policy

It was going to be
`when: [{field: intent.action_types, not_in: [advisory]}]`, `on_violation: no_match`.
That cannot express what's wanted, because a policy has **one** `on_violation` and
this needs two behaviours:

| intent | wanted |
|---|---|
| `[lookup]` — nothing we serve | `no_match` |
| `[lookup, advisory]` — partly served | proceed, and name the gap |

So supported-action scope is **not a policy**. It's a capability check that computes
what's unsupported by subtracting what the deployment serves from
`intent.action_types` — which is the right home anyway, since scope is *"no match
against the reachable capability set"* rather than a policy violation. That's how one
deployment avoids permanently refusing what another participant could answer.

Detail in 0004, since it lives with the service.

### Why `political-controversial` ships strict

The three repos disagree on protest/advocacy: `bharat` treats farmer complaints as
agricultural, `mh` rejects complaint-drafting as non-agricultural. DSS ships the
**strict** default, and the reason is the append-only asymmetry:

- strict default → a tenant wanting complaints allowed must **override the body**,
  which shows up in review as a deliberate loosening
- permissive default → a tenant wanting them rejected **appends one line**

Cheap operations should be the safe ones. And a tenant who never considers the
question gets the cautious behaviour rather than the permissive one.

---

## What is *not* a policy

Working through the three tenants surfaced a distinction worth stating, because two
of their hardcoded prompt rules do **not** belong in the policy set:

**Amul's dairy vocabulary** — `ભાવફેર` (price differential), PD, `બોનસ`, DCS, society
— is a bilingual allowlist in their prompt today. Writing it as a policy produces
`on_violation: proceed`, which is incoherent: a policy fires to *restrict*, and
Amul's need is to *widen* what counts as in-domain.

**BV's scheme-acronym allowlist** (MIF, PM-KMY, CDP, NMEO-OS, e-NAM, RWBCIS, NBHM…)
is the same shape.

Both are **vocabulary**, and vocabulary belongs in the domain taxonomy that feeds
intent classification. Once intent maps *"મારું ભાવફેર કેટલું છે?"* to `dairy`, no
policy is involved — the query is simply in-domain.

**The rule: policies restrict, the taxonomy grants, and neither can do the other's
job.** Adopter *restrictions* — a language allowlist, a locally-banned substance —
are genuinely policies. Adopter *vocabulary* is not.

The taxonomy is out of scope here; noted in Follow-ups.

---

## JSON Schema — `docs/schemas/policies.schema.json`

Dialect **2020-12**: `$schema: "https://json-schema.org/draft/2020-12/schema"` — note
`https` and **no** trailing `#`. draft-07's URI is `http` *with* `#`, and mixing them
is a silent-misconfiguration source.

Hand-authored rather than generated from Pydantic, because it's a **published
contract**: `docs/DSS_ARCHITECTURE.md` §4.2 has adopters validating their own
`/config` independently of DSS reachability. A tier-1 test asserts the Pydantic models
and the JSON Schema accept and reject the same documents, so they cannot drift.

Encodes structurally: exactly-one-operator per condition, the reserved-vs-namespaced
name pattern, and `overridable: false` ⇒ `on_violation` not replaceable.

---

## The loader — `src/dss/config/policy_loader.py`

Runtime validation is **Pydantic only** — `yaml.safe_load` → `PolicyPack(**data)`,
mirroring `amul`'s `_load_from_yaml` + `PipelineConfig`
(`app/llm_core/runtime.py:35-40`). Two corrections to that precedent, both cases where
a config mistake currently produces **wrong behaviour instead of an error**:

**(a) `extra="forbid"`.** An adopter misspells a field:

```yaml
- id: amul/local-pesticides
  on_violaton: reject      # missing the 'i'
```

Pydantic ignores unknown keys by default, so the model builds, `on_violation` falls
back to its default, and the policy does something other than reject. Nobody is told.
With `extra="forbid"`, boot fails naming the field. amul's `PipelineConfig` sets no
`extra` policy, so its YAML has this hole today.

**(b) Raise when a config path is set but the file isn't there.** amul's loader:

```python
if path and os.path.exists(path):
    PIPELINE = _load_from_yaml(path)      # use the file
else:
    PIPELINE = synthesize_from_env()      # quietly build a different config
```

An operator points `POLICY_CONFIG_PATH` at `/config/policy.yaml` when the file is
`policies.yaml`. Nothing is found, the `else` branch runs, and the app boots on a
**completely different configuration** — the authored pack never loads, with one INFO
line as the only clue.

| situation | meaning | behaviour |
|---|---|---|
| path **not set** | "I have no custom config" | use DSS defaults |
| path **set**, file missing | "I have a config" + it isn't there | **raise** |

(Both `.yaml` and `.yml` are accepted; the extension was never the issue.)

### Merge and precedence

Three levels, highest wins:

1. DSS policies with `overridable: false`
2. DSS policies with `overridable: true`
3. adopter-added policies

---

## `validate-config` — how adopters check their pack

The DSS ships as a container, so adopters have no Python environment with our
tooling. `docs/DSS_ARCHITECTURE.md` §4.2 currently says they run `ajv` or
`check-jsonschema` in their own CI, which assumes a toolchain we don't ship — and
lets them validate against a schema version that doesn't match their image.

Instead, a subcommand in the image itself:

```bash
docker run --rm -v ./config:/config dss:1.2.0 validate-config
```

Same binary, same validation code as the running container, no version skew, runs in
any CI that can run docker. Exit 0, or errors naming the offending lines.

The published JSON Schema still ships — for editor autocomplete and for adopters who
prefer their own tooling. **The architecture doc needs updating** to match.

---

## What an adopter actually does

**Add a rule** — write a namespaced policy in `/config/policies.yaml`. No code, no
DSS release, no prompt file.

**Tighten a DSS policy** — append via `extends:`. Works even on
`overridable: false` policies, which is the point: a locally-banned pesticide is a
strengthening, and safety policies should take those freely.

**Loosen a DSS policy** — replace the body, only where `overridable: true`.
Deliberate, and visible in review.

**What they cannot do** — weaken `unsafe-illegal` or `role-obfuscation`, remove a
DSS-shipped signal, use a bare name, or write a policy against a field their
checkpoint doesn't have. All are startup errors naming the offending line.

### MV wants protest/advocacy rejected

DSS ships strict, so MV needs **nothing**. Had DSS shipped permissive:

```yaml
- id: political-controversial
  extends:
    signals:
      - drafting protest letters or complaints against government bodies
```

Four lines. And because `extends:` appends, MV cannot accidentally delete the
partisan-content signals DSS ships.

### BV wants farmer complaints allowed

The opposite direction, and it needs a real override:

```yaml
- id: political-controversial
  on_violation: reject
  description: Partisan comparison and voting advice only.
  signals:
    - comparing political parties or leaders on agricultural policy
    - voting advice
  # protest/advocacy deliberately absent — grievance drafting is farmer welfare
```

BV must restate the whole policy, so the loosening is explicit rather than a silent
omission.

### Amul's language allowlist

Deterministic, no model call — cheap, exact, and unspoofable by prompt injection,
which a prose language rule in a system prompt is not:

```yaml
- id: amul/supported-languages
  checkpoint: moderation
  evaluation: deterministic
  on_violation: reject
  when:
    - field: turn.target_lang
      not_in: [en, gu]
```

### Honest limits

- **Prompt quality is still prompt quality.** `signals` render into a prompt, so a
  vague signal gives a vague judgment. BV's hand-tuned 8 KB prompt has a rhetorical
  structure YAML fragments don't fully reproduce. The `examples`-as-fixtures mechanism
  is the mitigation: an adopter can *measure* whether their signal works.
- **A semantic loophole passes every structural check** — see `overridable` above.
- **No `enabled: false`.** Removing a DSS policy means overriding it with a no-op
  body — deliberate friction on safety-relevant rules.

---

## Tests — test-first

Tier 1 throughout: plain Python, no LLM, no network. Mirroring `src/dss/` —
`tests/unit/config/`, `tests/unit/core/policy/`.

| # | red test | makes green |
|---|---|---|
| 1 | a `Condition` with two operators raises | `Condition` + exactly-one validator |
| 2 | a deterministic policy with empty `when` raises | the discriminated union |
| 3 | an unknown `checkpoint:` raises | `Checkpoint` enum binding |
| 4 | a minimal valid YAML loads to a `PolicyPack` | `policy_loader.load` |
| 5 | the shipped default pack loads and validates | `policies.yaml` |
| 6 | each of the eight validation failures, one test each | the startup checks |
| 7 | `intent.confidance` error names the field and suggests a fix | path validation |
| 8 | `tool.requires_auth` at moderation names the in-scope roots | root scoping |
| 9 | adopter merge and precedence across the three levels | merge resolution |
| 10 | a policy evaluates against a **throwaway** context model defined in the test | proves the evaluator names no root |
| 11 | Pydantic and JSON Schema accept/reject the same documents | `policies.schema.json` |

Step 5 catches a schema that cannot express its own default pack — the failure a
schema-only change would ship blind. Step 10 is what makes checkpoint-agnosticism a
tested property rather than an intention. Step 11 is the anti-drift test: without it
the published artifact stops describing what the DSS accepts.

Every validation test asserts the **error names the offending field or value**, not
merely that it raised. "Validation failed" costs an adopter an hour; `intent.confidance`
costs a minute.

---

## Verification

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run pytest
uv run check-jsonschema --schemafile docs/schemas/policies.schema.json \
    src/dss/config/defaults/policies.yaml
```

A schema is only worth having if bad config fails, so each of the eight checks above
gets a YAML fixture fed through the loader with the error asserted.

---

## Follow-ups

- **0004** — evaluation, the moderation service, `ModerationDecision`.
- **Adopter-extensible domain taxonomy.** Needed before Amul's dairy vocabulary or
  BV's scheme acronyms can leave prose. Policies restrict; the taxonomy grants.
- **Architecture doc**: §4.2's adopter-validation story changes to `validate-config`;
  the Policy primitive gains `overridable`/`extends`; the operator table and
  field-path contract are durable design worth recording.
- **`in` on collection fields** — intersects or subset? Ship without it if unsettled.
