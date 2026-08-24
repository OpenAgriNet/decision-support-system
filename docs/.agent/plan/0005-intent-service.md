# 0005 — Intent service and the domain taxonomy

**Issue:** #3 · **Status:** spec under review · **PRs D1 and D2**

Depends on [`0002-intent-contract.md`](./0002-intent-contract.md) — this produces the
`Intent` that spec defines, and amends it in two places (see Amendments).

## Context

Three specs are under review: `0002` (the `Intent` type), `0003` (policy schema), `0004`
(moderation service). Between them they define what an intent *is* and what moderation
*does with one* — but nothing produces an `Intent`.

**That split was deliberate.** The focus was policy and moderation first, and those need
the `Intent` *schema* to validate field paths against — so the schema landed in PR A and
the service was left for later. This is that later. What it adds is the producer: `0003`'s
`ModerationContext` takes `intent: Intent`, `0004`'s capability check reads
`intent.action_types`, and until something classifies a query those are fed by hand.

The second gap is what makes the three named tenants differ. `0003` §"What is not a
policy" establishes the rule — **policies restrict, the taxonomy grants** — and then
defers the taxonomy to follow-ups. Without it, Amul's dairy vocabulary and BV's scheme
acronyms have nowhere to live, and Amul/BV/MV are identical deployments.

**Outcome:** a basic intent classifier, and the configurable taxonomy that lets adopters
widen what counts as in-domain without a code change.

---

## Two facts established by exploration

Both verified rather than assumed, and both changed the design.

**1. There is no network config tier, and no mechanism for one.** Config reaches a
deployment two ways only: baked into the image (§5.3, "same immutable image across all
adopters") or mounted at `/config` (§4.1). No pull, no fetch, no sync, no API. The DPG
publishes JSON *Schemas* at a stable URL — not config content. So §5.2's promise that
"extensions that become widely used get promoted" is, today, an image version bump.

**2. `confidence` has no meaning in this system yet.** Zero confidence scores exist in
production code across all three sibling repos — the string does not appear in a single
line of executable code. The `0.87` in the docs is illustrative with no producer. The only
semantics anywhere is `0002`'s comment: *"Below this, ask a clarifying question rather than
guessing."* `DSS_ARCHITECTURE.md` §8.3 still lists "confidence categories" as open.

---

## Language posture for this slice

**Intent receives English queries.** Multilingual input is a separate story. But language
stays a first-class citizen in the design rather than being bolted on later:

- `turn.source_lang` and `turn.target_lang` flow through and are **not** dropped as unused
  — the same rule `0004` applies to moderation.
- The classifier reads **`turn.original_query`**. With English-only input it equals
  `enriched_query` today, but it is the right seam once translation exists.
- The taxonomy's `terms:` list is a flat list of spellings with no language key, so adding
  Gujarati and Hindi spellings later is **data, not a schema change**.

An **LLM structural test** (tier 5 — `tests/llm/structural/`, real or replayed call,
asserting shape rather than wording, gated every commit) checks that the prompt's example
language matches the input language. This is `0004`'s rule, which exists because Amul's
prompt is dense with Gujarati while its pipeline delivers pre-translated English, so those
examples rarely match anything.

**Accept knowingly:** the accuracy corpus is English-only, so its number must be labelled
`en` and never read as a general claim.

---

## Scope

**In:** the taxonomy models, the `TaxonomySource` port, a YAML adapter and the shipped
default taxonomy, layer-ownership validation and startup checks, an LLM classifier, the
prompt built from the taxonomy, and per-function LLM settings.

**Out — and the first of these is a reversal worth naming:**

- **The deterministic vocabulary matcher.** An earlier draft had a regex-matching layer
  running before the LLM, justified as "free and exact". Cut — see below. It returns as a
  separate optimisation story when there are latency numbers to justify it.
- §5.2's session cache, global frequency cache, and embedding-similarity layers.
- A network taxonomy layer, and the network refresh path (both scheduled — see Follow-ups).
- Multilingual input, enrichment (`enriched_query` stays `== original_query`), and any
  consumer of `intent.entities`.

### Why the deterministic layer was cut

It looked free. Counting what it actually bought:

It could skip the LLM only when a query contained a registered term **and** that term
carried an `action_type_hint` — because both `domains` and `action_types` must be filled
for the LLM to be skippable. Exactly one term in the drafted taxonomy had a hint. Every
other query paid for the LLM call anyway.

Against that: a Unicode-correct matcher (non-trivial — see below), a term-collision rule,
`action_type_hint` on the term model, and 9 of 23 tests.

So it was optimisation shipped before the thing it optimises, and its removal costs nothing
this slice: **the taxonomy still makes adopters differ**, which was the actual goal, because
`terms:` now feeds the prompt instead of a matcher.

**One finding is worth keeping even though the code is cut.** A tokenizing matcher is
silently broken for Indic scripts:

```python
re.findall(r"\w+", "મારું ભાવફેર કેટલું છે?")
# ['મ', 'ર', 'ભ', 'વફ', 'ર', 'ક', 'ટલ', 'છ']     ← ભાવફેર shatters
re.match(r"\w", "ા")   # None — Gujarati vowel signs are Mc/Mn, excluded from \w
```

The working form is `re.search(rf"(?<!\w){re.escape(norm(term))}(?!\w)", norm(text))` with
`norm = NFC + casefold`, verified on 8 boundary cases including `ભાવ` correctly *not*
matching inside `ભાવફેર`, `pd` not matching inside `pdf`, and Gujarati `ભાવ` not matching
Devanagari `भाव`. Whoever builds the matcher should not have to rediscover this.

---

## Design

### Where things live

`0003` inverted the dependency for policies — `config/` exposes, `orchestration/` wires,
`core/` takes an argument. The same inversion applies, with one addition: the taxonomy gets
a **port**, because its storage is going to change.

```
ports/taxonomy.py             class TaxonomySource(Protocol):
                                  def load(self) -> Taxonomy: ...

adapters/taxonomy/yaml_source.py    ships now — reads the DSS defaults and the
                                      adopter's /config, resolves them into one Taxonomy
adapters/taxonomy/redis_source.py   a later slice, with its own ADR

core/taxonomy/models.py       Taxonomy, Domain, Term — the types
core/intent/service.py        classify(turn, taxonomy, llm, settings) → Intent
                                takes a Taxonomy; never imports config or an adapter

orchestration/                loads once via the port, passes the object in
```

**Why a port rather than a loader function.** Storage is explicitly undecided — database
and messaging are open items, and choosing one is an ADR-worthy decision, not a spec
detail. A port lets this slice ship without making that call: YAML now, Redis or a DB later
as an adapter swap plus an ADR, with **zero change to `core/`**.

It also keeps `0003`'s `validate-config` working. That subcommand validates a mounted
`/config` directory — a DB-backed taxonomy has nothing to mount, so shipping DB-first would
mean reworking a primitive already specced in a PR under review.

**Why the models get their own `core/taxonomy/` package.** Not because knowledge ingestion
shares them — that is a different service. Because the taxonomy has a **different lifecycle
from the intent service that reads it**: loaded independently, validated independently, and
refreshable at runtime. Types owned by `core/intent/` would make that lifecycle look like
intent's, and `core/routing/` would later import `intent` for a type neither owns.
`core/shared/` is the precedent for a non-function core subpackage.

*A convention call worth confirming in review:* the `core/<function>/` layout enumerates
logical functions, and `taxonomy` is not one. `core/shared/taxonomy.py` is the alternative.
Only paths change either way.

### Runtime refresh — designed for, adopter-scoped

The refresh capability is what the port is for. This slice loads once at startup and records
the constraint that makes a later refresh safe:

**`Taxonomy` is immutable** — `tuple[...]` throughout, per `0002`'s own caveat that `frozen`
does not deep-freeze a list. Refreshing is an **atomic reference swap**, not a mutation: a
turn already in flight keeps the object it started with and cannot observe a half-updated
taxonomy. Get this wrong — a mutable object updated in place — and the bug is a turn
classified against two different taxonomies, which is unreproducible.

**What refresh is for, stated accurately:** an **adopter** changing their own vocabulary
without a restart. Amul adding a dairy term should not need a redeploy. That alone justifies
the port.

**What it is not for yet:** propagating network governance. There is nothing to propagate
from — no distribution mechanism exists (fact 1). An earlier draft claimed refresh was
"load-bearing for the governance model"; that overstated it, and the network refresh path is
scheduled as its own work in Follow-ups.

No trigger, no endpoint, no cache invalidation this slice: there is nowhere to hang a reload
endpoint while REST/gRPC/in-process is undecided.

### The taxonomy document

Shape is storage-independent — the same structure serialises to YAML now and to a Redis
value or DB rows later. Shown as YAML because that is what the first adapter reads.

**One document, not two.** An earlier draft split definitions (what dairy *is*) from
activations (whether this deployment *serves* it) into separate files, on the argument that
a future network API could not be authoritative for both. That argument was wrong: the port
already resolves it. When definitions arrive from an API, that adapter replaces where the
*DSS layer* comes from — the adopter's `/config` layer does not move, and adopter-wins
precedence still applies. An adopter that does not serve soil-health publishes a taxonomy
without it. No second file, no `active_domains` list.

**The one thing this genuinely gives up:** "we do not serve dairy" and "dairy does not
exist" become indistinguishable, because narrowing scope is expressed by omitting a
definition rather than by recording a decision. That only bites once a network layer
publishes definitions the adopter must then re-assert a removal against — and there is no
network layer and no distribution mechanism. Recorded in Follow-ups against the network
layer, where it belongs.

```yaml
version: 1
domains:
  - id: dairy
    owner: dss
    description: Milk collection, cooperative membership, and dairy payments.
    subdomains:
      - id: cooperative-payment
        description: Price differential, bonus, and dividend on milk supplied.
    terms:
      - price differential
      - PD
      - bonus
      - dividend
      - DCS
      - ભાવફેર          # Gujarati spellings ship now, used once input is
      - બોનસ            # multilingual — data, not a schema change
      - ડિવિડન્ડ
    examples:
      - What is PD?
      - How is the price differential calculated?

  - id: agri-scheme
    owner: dss
    description: Government agriculture schemes — eligibility, benefits, and status.
    subdomains:
      - id: scheme-status
        description: The caller's own application or installment status.
      - id: grievance
        description: Complaints about a scheme payment or application.
    terms: [PM-KMY, PMKMY, e-NAM, eNAM, NMEO-OS, RWBCIS, MIF, CDP, PKVY]
```

| field | why it exists | evidence |
|---|---|---|
| `id` | the join key across domains, subdomains, and later Skills/tools | §5.2 |
| `owner` | which layer contributed it — drives precedence, below | this spec |
| `description` | renders into the prompt; **`max_length` bounded** | the anti-8KB ratchet |
| `terms` | tenant jargon the model would not otherwise know | Amul prose, bharat aliases |
| `subdomains` | fixture categories fold in here | bharat's 66 labelled cases |
| `examples` | few-shot for the classifier | all three sibling prompts |

**`terms:` feeds the prompt, not a matcher.** This is the whole reason it survives the
deterministic layer's removal. Amul's prompt today carries `ભાવફેર`/PD/`બોનસ`/DCS as prose
precisely so the model recognises the tenant's jargon; structured `terms:` is the same job
done as data — editable per adopter, and validated.

**No per-term `lang` key** — one flat list holding every spelling. Amul's `gu_terms` and
bharat's `scheme_aliases` independently converged on exactly this, and it is what makes
multilingual a data change rather than a schema change.

**Cut deliberately:** `action_type_hint` (existed only to fill `action_types` on the
now-removed skip path); per-term `weight`; Amul's `type: hardcode|ask` (that is `0003`'s
`clarify` policy — importing it puts a restriction inside the thing that only grants);
Amul's free-text `rule` (recreates the 8KB prompt); `regex`.

`Taxonomy` uses `tuple[...]`, not `list[...]` — required by the refresh model above.

### Layer ownership and precedence

Every entry carries `owner`. Precedence runs **network > adopter > DSS** — a deployment may
localise what the DSS ships, but may not overrule what the network governs.

| a layer may… | its own entries | DSS entries | network entries |
|---|---|---|---|
| **adopter** | add, override, remove | **override, remove** | nothing — startup error |
| **network** | add, override, remove | override, remove | — |
| **DSS** | add, override, remove by release | — | nothing |

**Why adopters may remove a DSS domain.** A deployment that genuinely does not serve dairy
should not carry it — forcing the domain to stay and be worked around is worse than letting
it go. The alternative considered was override-only, with "effective removal" done by
overriding to an empty-terms domain; that is deletion with extra steps and a less-honest
config file.

**The risk this accepts, and its mitigation.** Domain ids are the join key that Skills and
MCP tools will be tagged with (§5.2), so removing one can orphan whatever references it and
surface as silently empty retrieval. Removal therefore emits a **startup warning naming the
removed id**, so the operator sees it rather than discovering it through empty results. A
hard reference check is not possible yet — nothing tags domains with a taxonomy id today, so
the check would be a no-op that looks real. Recorded in Follow-ups for whichever slice adds
tagging.

**Why network entries are untouchable by adopters.** This is the asymmetry that makes
central governance mean anything. If a deployment could delete a network-governed tag, the
DPG's promotion and deprecation lifecycle (§5.2) would be advisory. It is also the direction
that matters in practice: adopters loosening their own scope is fine, adopters opting out of
network vocabulary is not.

`network` is a value nothing produces yet — two layers can write today (image, `/config`).
**The rule and its validation are real from day one anyway**, so when a distribution
mechanism arrives there is no schema change, no validation change, no migration.
Retrofitting ownership onto entries that never carried it costs far more than shipping the
field unused.

Attempted removal or override of a higher-precedence layer's entry is a startup error naming
the owning layer.

`overridable` has **no analogue here**, and structurally cannot: it exists because policies
restrict and adopters must not loosen safety. A taxonomy only grants, and moderation still
runs on everything intent recognises — widening what is *understood* cannot widen what is
*permitted*. Narrowing what a deployment *serves* remains `0004`'s `NO_MATCH` capability
path.

`extends:` is reused verbatim from `0003` — which closes `0003`'s own taxonomy follow-up.

### Ordering and provenance

Layer order follows precedence — **network → adopter → DSS** — and `Intent.domains` is
emitted in that order. Only adopter and DSS exist today, so in practice it is
adopter-before-DSS until a network layer arrives.

**Ordering is provenance, not primacy.** It records which layer's vocabulary matched first;
it does **not** license a consumer to read `domains[0]` and ignore the rest. The `0002` case
still holds — *"price of potato and milk"* returns both domains with neither demoted, and
routing must serve every domain in the list. A consumer that stops at `[0]` gives a farmer
half an answer to a two-part question, which is exactly what `0002` rejected
`primary_domain` to prevent.

Provenance is also carried explicitly, so consumers act on it rather than inferring it from
position:

```python
domains: list[str]                    # [amul-dairy, mandi-prices]
domain_sources: dict[str, str]        # {amul-dairy: adopter, mandi-prices: dss}
```

**Two limitations, both accepted rather than worked around:**

- `domain_sources` is a dict, and `0003` rejects dict fields as policy targets — so a policy
  **cannot** test provenance. *"Reject when the matched domain came from adopter
  vocabulary"* is unexpressible this slice.
- Nothing structurally prevents a consumer reading `domains[0]`. Test 15 asserts
  `classify()` preserves every domain, but no test can catch a consumer that does not exist
  yet. **This is weaker than `0003`'s throwaway-context-model guard, and is stated as such
  rather than implying the prose is enough.** The obligation lands on whichever slice adds
  routing.

### The classifier

One LLM call, structured output, taxonomy rendered into the prompt.

```
classify(turn, taxonomy, llm, settings) → Intent
```

The prompt has **two parts with different owners**, and separating them is the whole
design:

| part | example | lives in | adopter may edit |
|---|---|---|---|
| **output contract** | the field list, "return JSON shaped like this" | code | **no** |
| **domain data** | domains, descriptions, terms, examples | the taxonomy | yes, as data |
| **judgement guidance** | "when unsure, prefer crop-advisory over mandi-prices" | `/config/prompts/intent.md` | **yes, versioned** |

**Why guidance must be adopter-editable.** The evidence is direct: bharat's moderation
prompt says *"when in doubt, decline rather than allow"*; mh's says *"be generous: when
unsure, classify as valid_agricultural"*. Same code, opposite instruction, and both are
right for their deployment. That is not data and not a contract — it is judgement, and it
belongs in config. An earlier draft of this spec had no prompt file at all, which made this
divergence inexpressible.

**Why the output contract must not be.** An adopter who edits the field list breaks
structured output, and the failure surfaces as bad classifications rather than a clear
error. It stays in code, where the `LLMProvider` adapter owns it.

**The risk this reopens, and the two guards on it.** Editable prompt files are exactly how
the siblings grew 8KB prompts that nobody prunes — Amul's dairy vocabulary appears **three
times in one file**. So:

1. **The guidance file carries no vocabulary and no output format.** Vocabulary is
   `terms:` in the taxonomy; format is code. A term added to the prompt file instead of the
   taxonomy is a review finding.
2. **A size budget, enforced in CI** by the LLM-structural test, plus `max_length` on
   taxonomy descriptions.

The file is versioned like any other config — YAML/Markdown under `/config`, reviewed by
PR, and it is a **seventh config primitive** alongside the taxonomy.

Skills are out of scope for this slice.

Per-function LLM binding, same five knobs moderation has, set independently:

```
DSS_INTENT_MODEL        DSS_INTENT_TEMPERATURE = 0.0    DSS_INTENT_TIMEOUT_S = 5
DSS_INTENT_RETRIES = 1  DSS_INTENT_MAX_TOKENS  = 512
```

`max_tokens` is explicit because mh's `max_tokens=32` truncates its own JSON output.

### What `confidence` means

**The model returns a float, and this spec says plainly what that float is: an
ask-or-answer gate, not a calibrated probability.**

Two rejected alternatives, because the reasoning is the useful part:

- **A coarse `high|medium|low` label mapped to floats.** Models are more reliable at buckets
  than at inventing decimals, so this looked better. It isn't: every bucket needs a matching
  threshold constant somewhere, so adding one is a **two-place edit where forgetting the
  second is silent** — precisely the Amul `0.80`-in-two-files defect `0002` criticised.
- **Deriving it from observable signals.** With the LLM as sole classifier there is almost
  nothing to observe. Any formula would be invented, which is the magic-number problem
  again.

And the decisive point: **`0003` asks exactly one question of this number** —
`confidence < 0.5`. A three-state enum encodes three states to answer a one-bit question.
The extra states buy nothing and cost a coupling.

**Multiple domains does not mean low confidence.** *"What's the price of potato and milk?"*
is two domains at full confidence — the farmer asked two things and both get answered. That
is different from an *ambiguous term*, where one thing was asked and it is unclear which.
Conflating them would downgrade the two-part question and produce the half-answer `0002`
fought.

**This partially closes an open architectural item.** §8.3 lists "evaluation thresholds,
confidence categories, and human-escalation requirements" as open. This is the first thing
in the system to produce a confidence number, so it settles the intent-classification part
of that — **said out loud here rather than closed silently**, which is the failure mode.

The threshold itself stays in `0003`'s `low-confidence-intent` policy, sole owner.

### Startup validation

In the spirit of `0003`'s eight checks — each prevents a taxonomy that **looks configured
and does nothing**. The highest-value four:

- **An adopter touches a network-owned entry** — raises, naming the owning layer. This is
  what makes central governance real rather than advisory.
- **A removal or override targets an id that does not exist** — raises rather than silently
  no-op'ing, which is how a typo'd override reads as "it worked". This is the check that
  catches `mandi-price` for `mandi-prices`: an adopter *intending* to override a DSS domain
  gets an error rather than a second, empty domain.
- **A DSS domain is removed** — a **warning**, not a failure, naming the removed id, since
  removal is legitimate but can orphan Skills and tools tagged with it.
- **A domain with no `description`** — near-unclassifiable while looking fully configured.

Plus: duplicate ids, subdomain id uniqueness, empty `terms` **and** empty `examples` on the
same domain, unknown fields (`extra="forbid"`), and a set config path whose source is
missing (`0003`'s rule — "I have a config" plus it isn't there must raise, never fall back
to defaults).

Validation lives behind the port, not in the YAML adapter, so a later Redis or DB source
inherits every check rather than reimplementing it.

### What changes on `Intent`

`subdomains: list[str]` is **unchanged**. Subdomain ids become globally unique (a startup
check), so a flat list is unambiguous by construction; the parent-domain invariant is
enforced in `classify()`, not in the model — which must not know the taxonomy exists.
Rejected `dict[str, list[str]]` (`0003` rejects dict policy targets) and qualified
`"dairy/cooperative-payment"` strings (path syntax in a field `0002` documented as plain
names).

`domains: list[str]` keeps its type and gains a **documented ordering guarantee** — a
contract addition, not a shape change.

`domain_sources: dict[str, str]` is **one new field**, defaulting to `{}`.

---

## Two PRs, one spec

This spec covers both; the implementation splits at a clean seam. D1 has no dependency on
the classifier, and D2 consumes D1's output — so D2 lands against a reviewed taxonomy rather
than both being judged at once. Same reasoning that produced the A/B/C split.

| PR | contents |
|---|---|
| **D1 — taxonomy** | `core/taxonomy/models.py`, `ports/taxonomy.py`, `adapters/taxonomy/yaml_source.py`, the default taxonomy, layer resolution + ownership, all startup validation |
| **D2 — intent service** | `core/intent/service.py`, the prompt, `DSS_INTENT_*` settings, `domain_sources` on `Intent` |

---

## Amendments to specs under review

**`0002` — delete `intent_confidence_min` from `Settings`.** It duplicates `0003`'s
`low-confidence-intent` threshold: the same `0.5` in two places, the Amul defect `0002`
itself criticised. The policy owns it — the only field with a consumer, and two thresholds
would let intent suppress a query before the policy that decides what to do about low
confidence ever sees it.

`0002`'s tests 7–9 demonstrate the range-declaration principle on that field, so retarget
them onto `DSS_INTENT_TEMPERATURE` (`ge=0.0, le=2.0`) rather than losing the lesson.

**`0002` — add `domain_sources: dict[str, str]` to `Intent`**, defaulting to `{}`, and
document the ordering guarantee on `domains`. Not policy-addressable (`0003` rejects dict
targets) — a deliberate limitation, not an oversight.

**`0003` — `validate-config` covers the taxonomy too**, not only the policy pack. Same
subcommand, one more source.

**`0004` — `LLMProvider` gains a second consumer.** If PR C shapes the port around
moderation's response type, it needs to be generic. **Check when 0004's code lands.**

**No storage ADR in this PR.** The port is what defers it — `TaxonomySource` with a YAML
adapter commits to no database. The ADR comes with the slice that adds a second adapter.

**Docs owed** (deferred deliberately until the code lands): §5.2's singular
`primary_domain`/`action_type` sketch is superseded; the taxonomy becomes a **sixth** config
primitive, so "the 5 configuration primitives" in `DSS_ARCHITECTURE.md` §4.1 and in the
`config/` folder comment both need the number changed; §8.3's confidence item is partially
closed and should say so.

---

## Tests — test-first

**Core unit tests (tier 1 — `tests/unit/`, plain Python, no LLM, no network, gated every
commit) carry the bulk**, and legitimately: layer resolution and validation are pure
functions over loaded config.

**D1 — taxonomy.** All tier 1, in `tests/unit/core/taxonomy/` and `tests/unit/config/`.

| # | red test | makes green |
|---|---|---|
| 1 | a domain with no `description` raises at load | the model |
| 2 | duplicate domain ids raise | uniqueness check |
| 3 | duplicate subdomain ids across domains raise | the flat-list invariant |
| 4 | an adopter **overrides** a DSS domain's description and terms | precedence |
| 5 | an adopter **removes** a DSS domain — it disappears, with a warning | removal + its mitigation |
| 6 | an adopter touching a **network**-owned entry raises, naming the owner | governance |
| 7 | overriding or removing a nonexistent id raises rather than no-op'ing | the typo case |
| 8 | adopter `extends:` adds terms without dropping DSS ones | append semantics |
| 9 | a domain in both layers is ordered adopter-before-DSS | the ordering guarantee |
| 10 | a loaded `Taxonomy` cannot be mutated in place | the refresh precondition |
| 11 | a set config path with a missing source raises | `0003`'s rule |

**D2 — intent service.** Tier 1 in `tests/unit/core/intent/`, with `LLMProvider` mocked per
the tier-1 rule (mock `ports/` Protocols; never call a real LLM).

| # | red test | makes green |
|---|---|---|
| 12 | a mocked LLM response maps to a valid `Intent` | `classify()` |
| 13 | `domain_sources` names the layer each domain came from | provenance |
| 14 | a classified subdomain belongs to its parent domain | the invariant |
| 15 | a two-domain classification keeps **both** domains | no silent demotion |
| 16 | an out-of-taxonomy domain from the LLM is rejected or dropped | the taxonomy is the contract |
| 17 | `classify()` works against a **fake `TaxonomySource`** built in the test | the port seam |
| 18 | a mocked LLM timeout surfaces rather than yielding a bogus Intent | failure handling |

Test 6 is the one that matters most in D1 — the difference between governed vocabulary and a
free-for-all. Test 5 is its counterweight: it proves the *permitted* removal still works, so
the governance rule doesn't quietly become a blanket ban. Test 17 is the analogue of
`0003`'s throwaway-context-model test — it proves `core/` depends on the port, not the YAML
adapter, so a later Redis source needs no change to `core/`.

Every validation test asserts the **error names the offending id or field**, not merely that
it raised.

**Beyond tier 1, two suites and one diagnostic:**

- **LLM structural** (tier 5 — `tests/llm/structural/`, real or replayed call, asserts
  schema and fields only, never exact wording; every commit): the prompt's example language
  matches the input language, and the rendered prompt stays inside its size budget.
- **LLM golden/replay** (tier 4 — `tests/llm/golden/`, cassette-recorded responses; every
  commit): a small set of recorded classifications spanning single-domain, two-domain, and
  unrecognised-query cases, so the end-to-end path is deterministic in CI.
- **LLM eval** (tier 6 — `tests/eval/`, **scheduled only, never a merge gate**): the **34
  English cases** of the 66 in `bharat-oan-api/tests/fixtures/agrinet_single_turn.json`
  (verified: 34 en / 32 hi), relabelled with `expected_domains`, scored. The Hindi cases are
  held for the multilingual story; the score is labelled `en` and is not a general claim.

No tier 2 (adapter contract) or tier 7 (E2E smoke) work here: the YAML adapter's behaviour
*is* the validation logic already covered at tier 1, and there is no `UserTurn → response`
path to smoke-test until an entrypoint exists.

**The fixture mapping is itself a finding** worth recording: `video_search` is a **modality,
not a domain** (naive reconciliation would mint one); `grievance` sits against `0003`'s
`political-controversial` and is the cleanest illustration of *taxonomy grants, policy
restricts*; `shc_status` spans two domains — fixture evidence that `domains` had to be a
list.

---

## Verification

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run pytest                    # tiers 1, 4 and 5
uv run pytest -m eval            # tier 6, deliberately
```

Then the negative cases, since a taxonomy is only as good as what it refuses — each startup
check gets a fixture fed through the loader with the error asserted.

And the check that cannot come from a unit test: throw real farmer phrasings at `classify()`
and read the domains back. Classification quality is learned that way, not from test output
— the same argument `0004` makes for adversarial moderation queries.

`0003`'s `validate-config` must cover the taxonomy too:

```bash
docker run --rm -v ./config:/config dss:X.Y.Z validate-config
```

The framework-boundary check must still pass: `dss.core.intent` and `dss.core.taxonomy`
import `pydantic` but never `pydantic_ai` or `pydantic_graph`.

---

## Follow-ups

- **The deterministic vocabulary layer**, as an optimisation with latency numbers behind it.
  **Carry the Unicode finding forward** — the verified matcher and its boundary cases are
  recorded above so they are not rediscovered.
- **Network taxonomy layer and network refresh.** Needs a distribution mechanism first —
  none exists (fact 1). The `owner` field means this arrives without a schema change.
  **That slice owes the definition-vs-activation question**: today an adopter narrows scope
  by omitting a definition, so "we do not serve dairy" and "dairy does not exist" are
  indistinguishable. Once the network publishes definitions, a removal has to be re-asserted
  against each new version, and an explicit activation list starts earning its keep. It does
  not yet.
- **Runtime refresh trigger, and the removal audit trail with it.** The port and the
  immutability precondition ship here; the mechanism needs the entrypoint decision, since
  there is nowhere to hang a reload endpoint while REST/gRPC/in-process is undecided.
  Auditing belongs in that slice: a boot-time removal is reconstructable from the config
  that produced it, whereas a refresh-applied removal leaves no trace. Accepted cost until
  then — a domain absent at boot is unexplained.
- **Provenance is invisible to policies.** `domain_sources` is a dict and `0003` rejects
  dict policy targets. Whichever slice first needs it decides between a parallel list field
  and extending `0003` to address dict keys.
- **`intent.entities` has no consumer.** Whichever slice first reads entities owes the
  extraction quality work.
- **Domain taxonomy tags on Skills and MCP tools** — §5.2 requires them; nothing consumes a
  domain id outside intent yet. **That slice owes the reference check on removal**: once
  artifacts are tagged, removing a domain should be refused (or hard-warned) when something
  still references it. Today only a warning is possible, because there are no references to
  look for.
- **Routing must serve every domain**, not `domains[0]`. Unenforceable until routing exists;
  the obligation lands on that slice.

## Open, and worth deciding when the code lands

- **`core/taxonomy/` vs `core/shared/taxonomy.py`** — a convention call (above).
- **`LLMProvider` genericity** — depends on what PR C actually builds.
