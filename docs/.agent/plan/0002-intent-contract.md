# 0002 — Intent contract

**Issue:** #57 · **Status:** spec under review · **PR A of three**

## Context

PR #4 landed the Python substrate — `pyproject.toml`, package layout, the
framework-boundary check, CI. `src/dss/core/{intent,moderation,shared}` and
`src/dss/config/` exist as empty packages.

A design grilling settled how moderation and intent work. The original plan tried
to land the whole policy engine in one change; review found it too large to review
properly, so it is now **three PRs**:

| PR | contents | why this order |
|---|---|---|
| **A — this one** | `Intent`, `ActionType`, `Settings` | policies validate field paths *against* `Intent`, so it comes first |
| B | policy models, `policies.yaml`, JSON Schema, loader, checkpoint dispatch | needs A's types to validate against |
| C | moderation service — deterministic and LLM evaluation | needs B's policies to evaluate |

This PR is the smallest useful piece: the type that describes *what a farmer is
asking for*. Nothing consumes it yet, but its shape determines what policies can be
written, so getting it wrong is expensive later.

---

## Why intent is a separate concern from moderation

Today, in all three sibling production repos, there is **no intent recognition at
all** — the moderation call's `category` field doubles as the classifier. That
conflation causes two problems the design grilling set out to fix:

- **Moderation can't be configured without touching classification.** The category
  list lives in a Python `Literal[...]` *and* in prose inside an 8 KB prompt, so
  adding a category is a two-place edit and a prompt rewrite.
- **"Not agriculture" and "agriculture we haven't built yet" become the same
  answer.** `bharat-oan-api`'s prompt carries a long allowlist of scheme acronyms
  precisely because `invalid_non_agricultural` is being asked to encode capability
  scope alongside domain scope.

So: **intent classifies, moderation judges harm.** This PR is the classification
half's contract.

---

## The contract

### `src/dss/core/intent/models.py`

```python
class ActionType(StrEnum):
    ADVISORY = "advisory"        # "when should I sow wheat?"
    LOOKUP = "lookup"            # "what's today's mandi price?"
    TRANSACTION = "transaction"  # "book a soil test"


class Intent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    domains: list[str] = Field(default_factory=list)       # empty = nothing mapped
    subdomains: list[str] = Field(default_factory=list)
    action_types: list[ActionType] = Field(default_factory=list)
    entities: dict[str, str] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)
```

### Why `domains` is a list with no "primary"

An earlier draft had `primary_domain: str` plus a single `subdomain`. Review killed
it with one question: *"mandi price and milk price — what's the primary domain?"*

Every answer is arbitrary. First-mentioned is word order, not importance.
Highest-confidence is false, because the classifier is equally sure of both. And
whatever gets picked, the other domain is silently demoted — so if routing reads
only the primary, the farmer gets half an answer to a two-part question. A growing
taxonomy (fishery is next) makes overlap more common, not less.

### Why `action_types` is also a list

Same reason, same kind of example: *"what's the mandi price and should I sow
now?"* is `lookup` **and** `advisory`. An earlier draft had this singular one line
after arguing domains must be plural — inconsistent.

This is what makes **partial answers** possible later. If a farmer asks two things
and we can only do one, we answer that one and say so: *"I can't look up prices yet,
but on sowing…"* — instead of refusing everything, or answering half and staying
quiet about the rest.

#### Three different ways a question can go unanswered

Worth separating now, because they look identical if you lump them together, and the
list shape is what lets us tell them apart:

| what happened | what we say | example |
|---|---|---|
| **We won't answer** | a refusal | "how do I fake an insurance claim?" |
| **We didn't understand** | a question back | a garbled or ambiguous query |
| **We can't answer yet** | "not available yet" | "today's mandi price" before lookup is built |

The third one is the one that gets lost. If a mandi-price question is refused like a
harmful one, then nobody can answer *"how many real farmer questions are we turning
away because we haven't built that feature?"* — the number is buried in with the
spam and the abuse. That question is the roadmap.

It also matters across the network: this system runs in several deployments, and one
deployment shouldn't permanently refuse a question another one could answer. "We
can't do that here" is temporary; "we refuse that" sounds permanent.

### Empty means "found nothing", and that's a real answer

`domains: []` means the classifier didn't recognise the question. That's not an
error — it's an outcome worth acting on, by asking the farmer what they meant. Same
for `action_types: []`.

Empty lists rather than `None` keeps things simple: one check (`is it empty?`)
covers both "the field wasn't filled in" and "we looked and found nothing", instead
of policies having to test for two different kinds of nothing.

### How policies will read these fields

PR B adds conditions that test these fields. Since `domains` and `action_types` are
lists, list behaviour has to be defined — otherwise each policy author guesses:

| check | on a list | plain English |
|---|---|---|
| `is: empty` | length is 0 | we recognised nothing |
| `is: present` | length is 1+ | we recognised something |
| `contains` | one of them matches | at least one matches |
| `not_in` | none of them match | not a single one matches |

`not_in` meaning *"none of them match"* is the cautious reading, and that's
deliberate. If a farmer asks about something we partly support, we'd rather notice
the unsupported part than quietly serve half the question.

### `frozen=True`, and what it does not give you

An intent is a finding about a turn, not mutable state. But note the limit so
nobody relies on more than it provides: Pydantic's `frozen` blocks attribute
*assignment*, while a `list` field's contents stay mutable and the model is not
hashable. If genuine immutability or hashability is ever needed — for caching, say
— switch to `tuple[...]`. Not needed here, since nothing caches an `Intent` yet.

### `src/dss/config/settings.py`

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DSS_", env_file=".env")

    # Below this, ask a clarifying question rather than guessing.
    intent_confidence_min: float = Field(0.5, ge=0.0, le=1.0)

    # What this deployment can actually serve. Becomes the Network Consumer
    # Adapter's reachable-capability view later; a setting until then.
    supported_action_types: list[ActionType] = Field(
        default_factory=lambda: [ActionType.ADVISORY]
    )
```

Set `DSS_INTENT_CONFIDENCE_MIN=0.7` and it uses 0.7 instead of 0.5. Two details
that matter:

**The valid range is declared.** `confidence` is a 0-to-1 score, so
`ge=0.0, le=1.0` says so. Set it to `50` and the app refuses to start.

Compare `amul`, which has the same kind of threshold with no range declared — so
`AMBIGUITY_MATCH_THRESHOLD=50` is accepted, and every comparison against it silently
fails from then on. Nothing crashes; the feature just quietly stops working.

**The default is written in exactly one place.** `amul` writes its `0.80` in two
files (`app/config.py:272` and `agents/tools/terms.py:471`), so changing one and
missing the other is a live possibility.

An earlier draft of this made thresholds far more elaborate — a `thresholds:` block
in the YAML, plus a special type so a policy could refer to a threshold *by name*.
Dropped: it was machinery for a single number, and `pydantic-settings` already does
the whole job (read the env var, fall back to a default, check the type, check the
range).

---

## One open question this PR must settle: what `UserTurn` carries

Design review of the policy engine surfaced this, and it belongs in PR A because it
changes a root that every future policy is written against.

`docs/DSS_ARCHITECTURE.md` §3.0 specifies that moderation evaluates the
**`original_query` / `enriched_query` pair** — not just the final query. The reason
is real: a follow-up turn like *"can I grow it now?"* can't be judged in isolation,
and a rewrite that materially changes meaning is itself worth policy-checking, since
history is untrusted input.

If `UserTurn` carries a single `query` field, that documented requirement cannot be
expressed as a policy at all. And adding the second field later changes the shape
every policy references.

The earlier grilling settled the contract as `(original_query, enriched_query)` with
`enriched_query = original_query` until enrichment lands. So PR A should define
`UserTurn` in `core/shared/` with both fields — a stub value for the second, but the
right shape from the start.

**Scope note:** this pulls `UserTurn` into PR A. It's small (two query fields plus
session, langs, channel, user) and it's the other root policies address, so it
belongs with `Intent` rather than arriving mid-way through PR B.

---

## Plan

1. **Dependencies** — add `pydantic-settings` (absent from the venv today). `PyYAML`
   waits for PR B, which is the first thing that reads a YAML file.
2. **`src/dss/core/intent/models.py`** — `ActionType`, `Intent`. A module, not a
   package: two types don't earn a directory.
3. **`src/dss/core/shared/models.py`** — `UserTurn`, `UserDetails`, with both
   `original_query` and `enriched_query` per §3.0.
4. **`src/dss/config/settings.py`** — `Settings`.
5. **Tests** — `tests/unit/core/intent/`, `tests/unit/core/shared/`,
   `tests/unit/config/`.

### Test order — test-first

Each row is one red-green cycle; nothing is written before a test demands it.

| # | red test | makes green |
|---|---|---|
| 1 | `Intent(confidence=1.5)` raises | `Intent`, `ActionType` |
| 2 | `Intent(confidence=0.5)` has empty `domains` and `action_types` | the defaults |
| 3 | `Intent(unknown_field=1)` raises | `extra="forbid"` |
| 4 | assigning to `intent.confidence` raises | `frozen=True` |
| 5 | `UserTurn` requires both query fields | `UserTurn`, `UserDetails` |
| 6 | a non-BCP-47 `source_lang` raises | the language constraint |
| 7 | `Settings()` gives `0.5` and `[ADVISORY]` | `Settings` |
| 8 | `DSS_INTENT_CONFIDENCE_MIN=0.7` wins | env binding |
| 9 | `DSS_INTENT_CONFIDENCE_MIN=50` raises | the bounds |

All tier 1: plain Python, no LLM, no network, no files.

Worth also writing test 4b — that `intent.domains.append(...)` **succeeds** — as a
documented limitation rather than a bug. It records what `frozen` does and doesn't
give, so nobody later assumes an `Intent` is safe to share across threads or use as
a cache key.

---

## Verification

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run pytest
```

Then the negative cases, since a contract is only as good as what it refuses. All
four checked against the Pydantic version in the venv before writing this plan —
they behave as claimed:

```python
Intent(confidence=1.5)                     # out of range      → ValidationError
Intent(confidence=0.5, domains="dairy")    # str, not list     → ValidationError
Intent(confidence=0.5, typo=1)             # unknown field     → ValidationError
intent.confidence = 0.9                    # frozen            → ValidationError
```

And the caveat is real, not theoretical — this **succeeds** on a frozen model:

```python
intent.domains.append("mutated")           # frozen does not deep-freeze
```

which is why the plan says use `tuple[...]` if real immutability is ever needed.

```bash
DSS_INTENT_CONFIDENCE_MIN=50 uv run python -c "from dss.config.settings import Settings; Settings()"
# must raise, naming the field — not silently accept an unusable threshold
```

The framework-boundary check from PR #4 must still pass: `dss.core.intent` imports
`pydantic` but never `pydantic_ai` or `pydantic_graph`.

---

## The three specs

| file | covers |
|---|---|
| **`0002-intent-contract.md`** | **this one** — `Intent`, `UserTurn`, `Settings` |
| `0003-policy-schema.md` | policy models, `policies.yaml`, JSON Schema, loader, checkpoint dispatch, field-path validation |
| `0004-moderation-service.md` | deterministic + LLM evaluation, the barrier, `ModerationDecision` |

All three are written before any code. That order is deliberate: C's requirements
are what proved `action_types` must be a list, and B's field-path design is what
surfaced the `UserTurn` gap above. Reviewing them together catches that class of
problem before it is built into anything.

Per `README.md` in this directory, each spec is deleted when its change lands, with
durable design folding into `docs/DSS_ARCHITECTURE.md` or an ADR.

---

## Deferred to 0003 and 0004

Recorded so the reasoning isn't re-derived:

- **Policy models, `policies.yaml`, JSON Schema, loader** — 0003.
- **Checkpoint dispatch and the field-path namespace** — 0003. Both questions have
  been designed and verified against this repo's interpreter; PR B's spec carries the
  detail. The answers, briefly:

  **Dispatch: build no abstraction.** The problem wasn't the loader grouping by
  checkpoint — an index over loaded config is a loader's job. It was the loader
  *handing* policies to a service, i.e. knowing about it. Invert: `config/` exposes
  `policies_for(checkpoint)`, `orchestration/` pulls and wires. A
  per-checkpoint class would conflate two things that vary independently — input
  assembly (genuinely different per checkpoint) and evaluation (identical across all
  three), so the evaluation half would end up duplicated or extracted anyway.

  **Field paths: a context model per checkpoint whose fields *are* the roots.**
  `ModerationContext(turn: UserTurn, intent: Intent)`. Startup validation walks
  `model_fields`; runtime resolution is `getattr` down the same path — one structure
  for both, so they can't drift. Root scoping needs no separate mechanism: an
  out-of-scope root is unexpressible because there's no field for it.
  `field: tool.requires_auth` at moderation fails on the first segment, listing what
  *is* in scope.

  **Operator validity becomes a startup check too** — classify the resolved
  annotation (`ordered` / `text` / `scalar_enum` / `bool` / `collection`), then check
  the operator against a table. So `{field: intent.confidence, contains: x}` fails at
  boot, as does `{field: turn.channel, equals: sms}` — the operand is validated
  against the field's type via `TypeAdapter`, catching a second class of typo that
  operator-checking alone misses.

  **`contains` earns its place**, and not for list-vs-scalar reasons: `in`/`not_in`
  put the collection in the *operand*, `contains` puts it in the *field*. Different
  relations. Collapsing them would make `{field: X, in: [...]}` mean intersection or
  membership depending on a type the YAML author can't see.

  **`dict` fields are rejected as policy targets in v1** — `intent.entities` is a
  dict, and `lt` on a dict has no sensible meaning. Failing at boot beats a policy
  that silently never matches.
- **`ModerationDecision`, `Outcome`, `ReasonCode`** — PR C, since nothing produces a
  decision until the service exists.
- **Partial fulfilment rendering.** `action_types` being a list enables it, but a
  `PROCEED` that carries an unsupported-action note needs response composition to
  actually say *"I can't look up prices yet"*. Until that exists the note would be
  unread — a silent half-answer, the exact failure it exists to prevent.
- **Adopter-extensible domain taxonomy.** `domains` is `list[str]` here rather than
  a constrained enum precisely because the taxonomy is adopter-extensible and
  network-dependent — one deployment must not permanently refuse a query another
  participant could answer. Working through the tenants showed that adopter
  *vocabulary* (Amul's dairy terms, BV's scheme acronyms) belongs in the taxonomy,
  not the policy set: policies restrict, the taxonomy grants.
- **`supported_action_types` is a stopgap** for the adapter's reachable-capability
  view.
- **Adopters validate config with a subcommand in the image**, decided during this
  review: `docker run --rm -v ./config:/config dss:X.Y.Z validate-config`. The
  architecture doc currently says adopters run `ajv`/`check-jsonschema` in their own
  CI, which assumes a Python or Node toolchain we don't ship — and lets them validate
  against a schema version that doesn't match their image. Same binary, same
  validation code, no version skew, runs in any CI that can run docker. The doc needs
  updating; the published JSON Schema still ships, for editor autocomplete.
- **Config-path handling.** Also corrected during review: an earlier draft used a
  `.yml` vs `.yaml` typo as its example of a silently-wrong config path. Both
  extensions should of course work. The real failure is a *wrong path* — pointing at
  `/config/policy.yaml` when the file is `policies.yaml` — where the current sibling
  behaviour silently boots on a different configuration. If a config path is set and
  the file isn't there, raise.
