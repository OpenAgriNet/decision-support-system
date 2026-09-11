# ADR-0007: an advertised field named after a filterable path is content, not a vocabulary

- **Status:** ACCEPTED
- **Date:** 2026-09-11
- **Deciders:** DSS code owners
- **Consulted:** Product Owner
- **Informed:** Adopter engineering teams

---

## 1. Context and Problem Statement

`describe_capability` shows the planner model each candidate's filterable
fields and the values its provider advertises, so the model can resolve a
farmer's word to a value it cannot invent — "tomato" to `supportedCommodities:
78=Tomato`. ADR-0005's loop depends on this: the `select` tool's argument is a
bare `dict`, so nothing in the tool schema says what a given capability
accepts.

The advertised values come off the wire. `adapters/discovery/client.py` keeps
every `resourceAttributes` key that is not Beckn-structural, and
`core/planner/describe_capability.py` renders each non-empty **list** under
the heading `this provider serves only these values:`. The
`provider-invocation` skill then tells the model to pick from that list and
never invent a value.

That rule is wrong for one shape of advertised field, and a farmer felt it.
Asked "can i grow potato", then "i want to grow in pune", the planner sent
`topics: ["Package of practices", "Crop establishment"]` — labels lifted
verbatim out of the provider's own catalog. It should have sent `["Potato in
Pune"]`.

`topics` is not a vocabulary. The KnowledgeAdvisory pack declares it as a bare
array of strings with **no `enum`**, lists it in `discovery_fields` and
`indexable_paths`, and expects the caller to *compose* the filter from what
the farmer said. A resource publishing `topics` in its catalog is saying what
it holds so it can be found by it. Rendered as a closed list, that became an
instruction to answer every potato question with someone else's taxonomy.

So: how does the renderer tell a governed vocabulary from a provider
describing its own content?

## 2. Decision Drivers

1. **The farmer's subject must reach the provider.** A `topics` filter built
   from a catalog label is a worse query than no filter at all — it is
   confidently about the wrong thing.
2. **Do not take real vocabularies away.** The model genuinely cannot invent
   `78=Tomato` or `POTATO`. Over-correcting into "show less" breaks the case
   `describe_capability` exists for.
3. **Pack-agnostic.** The rule must not encode one pack's field names.
4. **Stay inside the existing seams.** `core/` reaches nothing outside itself,
   and the discovery adapter deliberately uses a skip-list rather than reading
   `profile.json` (see the note in `client.py`).

## 3. Considered Options

- **Option A — bucket by item shape.** A list of objects (`[{code, name}]`) is
  a vocabulary; a list of bare strings is descriptive text.
- **Option B — bucket by field name against the pack's filterable paths.** An
  advertised field whose name *is* a filterable path is the provider
  describing its own content; anything else is a vocabulary.
- **Option C — parse `attributes.yaml` and treat a field as closed only where
  the pack declares an `enum`.**
- **Option D — keep rendering everything, and fix it in the prompt** by
  telling the model which fields are free text.

## 4. Decision Outcome

**Chosen option: B**, with a supporting prompt change from D.

`describe_capability` receives the capability's `DomainSchema.filterable` and
drops any advertised field whose name matches one of those paths exactly.
Everything else renders as before.

```
topics              ∈ filterable(KnowledgeAdvisory)  → dropped
supportedCommodities ∉ filterable(MandiPrice)        → rendered  (governs commodity.code)
supportedParameters  ∉ filterable(WeatherObservation)→ rendered  (governs parameters)
agricultureSubjects  ∉ filterable(…subjectId)        → rendered  (exact match, so kept)
```

The reasoning is that a vocabulary and the filter it governs are two different
things — the vocabulary is the filter's *domain* — so catalogs name them
apart. A resource advertising under the very path you would filter on has
nothing to offer the model to choose from; it is publishing content.

Alongside it, `provider-invocation.md` gains the counterpart to its existing
"use a code from that list" rule: **a field you may set with no listed values
is free text**, to be written from the conversation — the subject and, when
given, the place. The two changes are one decision. Dropping the list without
saying what to do instead leaves the model with a settable field and no
instruction; saying it without dropping the list leaves a strong example
arguing the other way.

### 4.1 Positive Consequences

- `topics` now carries the farmer's subject. Verified live: the two-turn
  conversation above produces `topics: ["Potato in Pune"]`, with the crop
  carried forward from the earlier message.
- Every real vocabulary in the repo's recorded catalogs still reaches the
  model — including `supportedParameters`, which is bare strings.
- No new index, no new file read, no change to the discovery adapter. The
  renderer already had the schema in hand at the call site.

### 4.2 Negative Consequences

- **The model no longer sees which topics a provider actually covers.** It can
  compose a topic no provider holds. Accepted: a provider matching a composed
  topic loosely beats one answering a different question confidently, and the
  network — not the DSS — owns matching.
- **The match is exact, so a compound path's head is not caught.** A provider
  advertising `commodity: [...]` beside a filterable `commodity.code` would
  still render. Exact matching is deliberate: `agricultureSubjects` sits under
  `agricultureSubjects[].subjectId` and *is* a vocabulary, so a prefix rule
  would drop the crop list. Only the exact case has been seen on the wire.
- **A field that governs no filter at all is still rendered.** The known case
  is `geographicGranularity: ["Point"]`; catching it needs the opposite
  authority — which advertised fields are a vocabulary *for* something — which
  no pack declares. Pinned by an assertion in
  `tests/integration/adapters/discovery/test_advertised_reaches_the_planner.py`
  so the cost stays visible.
- **The prompt half is not deterministic.** Tier 1 and tier 2 pin what the
  model is shown; only the tier-5 structural test shows what it then writes,
  and it needs credentials to run.

## 5. Rejection Rationale

**Option A (item shape)** was implemented first and is wrong. The recorded
WeatherObservation catalog advertises `supportedParameters: ["Rainfall",
"Temperature"]` — a genuine vocabulary of bare strings the model cannot
invent. The existing tier-2 test caught it. Shape describes how a value is
encoded, not whether it is governed.

**Option C (parse the pack's `enum`)** is the most principled statement of the
rule and remains the right long-term answer. It is not chosen now: it means
parsing `attributes.yaml` into `DomainSchema`, which today reads only
`profile.json`, and it would not help the cases that matter most —
`supportedCommodities` and `supportedParameters` are provider-specific
subsets, absent from any pack `enum`, so they would be dropped exactly like
`topics`. Option B gets today's cases right at a fraction of the cost.

**Option D alone** leaves the provider's own labels in front of the model as a
worked example of what to send, directly beside an instruction not to. The
weaker signal loses often enough to matter, and there is no reason to show
them at all.

## 6. Revisit Triggers

- A pack declares which advertised fields are a vocabulary *for* which
  filterable path. That is the authority both this rule and the
  `geographicGranularity` wart are approximating — adopt it and drop the
  name heuristic.
- A provider advertises content under a compound filterable path's head
  (`commodity` beside `commodity.code`). Revisit exact matching then, and see
  the note in `_describes_own_content` for why it is exact today.
- The tier-5 structural test starts failing on a model upgrade. The prompt
  half of this decision is the fragile half.

## 7. Follow-up Actions

- **[DSS code owners]** None outstanding; code, prompt, and tests land in the
  same change as this ADR.

## 8. Notes

- The rule lives in `core/planner/describe_capability.py:_describes_own_content`,
  the prompt half in `config/defaults/skills/provider-invocation.md`.
- Prior context on `topics` being free text:
  `docs/.agent/plan/10-planner-agent-poc.md`, section on model-filled fields.
