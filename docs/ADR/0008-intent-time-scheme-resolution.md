# ADR-0008: Resolve Scheme Names Deterministically at Intent Time

- **Status:** ACCEPTED
- **Date:** 2026-09-10
- **Deciders:** DSS implementation

---

## 1. Context and Problem Statement

Farmers name a scheme however they know it: "makhana scheme", "PKVY",
"dhan dhaanya", "organic farming scheme". The network's providers know it by
its official name ("Central Sector Scheme for Development of Makhana"). Two
things need the official name — the planner, to fill a `select` request, and
the composer, to name the scheme back to the farmer — and neither gets it from
a colloquial mention.

Two properties of the pipeline shape the answer:

- **Discovery routes on `Ask.subject_categories` alone**
  (`core/provider_discovery/service.py`). `agriculture_subjects` reaches no
  routing decision, so canonicalizing it changes *what the planner sends*, not
  *who is discovered*. A mis-categorised ask, however, is routed to the wrong
  capability type and no downstream step can recover it.
- **The vocabulary already has an owner.** Providers advertise the values they
  serve via `describe_capability`, and the shipped provider-invocation skill
  tells the model to use a code from that list and "never one you recall from
  elsewhere". Anything the DSS holds about schemes is therefore a second
  source of truth for the same class of data, and must not be allowed to
  compete with the first.

## 2. Decision Drivers

1. Cost per turn. Most turns name no scheme; whatever this costs, it costs on
   all of them.
2. Testability. The intent slice is the most tier-1-tested component in the
   codebase and should stay that way.
3. Latency. Intent is on the critical path and runs concurrently with
   moderation (ADR-0003); the turn's cost is the slower of the two.
4. Not becoming a rival governed-code source.
5. A catalog an agriculture officer — not an engineer — can maintain.

## 3. Considered Options

1. **A deterministic resolver after classification**, over a tenant-mounted
   catalog.
2. **A tool call during intent classification** — widen `LLMProvider` so the
   classifier can call `find_scheme` mid-reasoning.
3. **The alias list in the intent prompt** — 71 aliases as prompt text.
4. **Wait for the network** to advertise a scheme vocabulary at discovery time.

## 4. Decision Outcome

Chosen option: **1, a deterministic resolver at intent time**, reading a
tenant-mounted CSV catalog through a `SchemeCatalog` port, applied between
`classify_intent` and `discover_providers`.

A closed set of exact strings is a lookup problem. Option 2 spends two or
three model round-trips on every turn to help the few that name a scheme,
fires only when the model chooses to call the tool, blows the 5s
`intent_timeout_seconds`, and moves intent's tests from tier 1 onto cassettes
— while still not resolving a misspelling like "makna", which the tool would
also miss. Option 3 grows linearly with the catalog and was rejected on the
context budget, though it remains the cheap fix if the residual gap in §5
proves to matter. Option 4 leaves the pre-discovery gap open indefinitely,
since the network's vocabulary arrives only *after* routing has happened.

The resolver is explicitly a **pre-discovery hint**. It rewrites
`agriculture_subjects` to the official name and nothing else; the planner
still takes governed codes from `describe_capability` alone.

Supporting choices, each following from the above:

- **CSV, not YAML**, against the repo's config house style: this file is
  domain data owned by an agriculture officer, scheme lists already live in
  spreadsheets, and one row per scheme reviews cleanly in a pull request.
- **Nothing ships in the image.** Which schemes a deployment serves is the
  tenant's decision. Unset → inert plus one boot warning; set-but-missing →
  raise, per `policy_loader`.
- **A port over a config file**, which is a first for this repo (the policy,
  skill and identity loaders have none). Justified by the intended swap to a
  network-backed catalog, which is an adapter concern.
- **Longest whole-token span wins.** Spans, not substrings: `mif` occurs
  inside "amplifier". Longest-first is unambiguous only because no alias is
  shared between schemes, which the adapter enforces by refusing to load a
  catalog where one is.
- **The ask's own subject is matched before the raw query**, because the query
  is shared by every ask in the turn.
- **`Ask` is unchanged.** No `resolved_code` field; `scheme_code` stays in the
  catalog as the stable row key for a future mapping onto the network's
  vocabulary.

## 5. Consequences

- `core/enrichment/` is created as the home for this and later reference
  resolution, matching the logical function named in §3 of the architecture
  doc.
- **The catalog must contain no bare commodity words, and nothing in the code
  enforces that.** `makhana` and `foxnut` were listed as aliases in the source
  document and are wrong: with `makhana` in the catalog, "makhana price in
  Patna mandi" resolves to a scheme. This became load-bearing in #36, which
  removed the category gate: an alias hit now overrides the classifier's
  category, and the one structural guard left is that a non-scheme ask is
  judged on its own extracted subject and never on the shared raw query.
  A guard on the catalog itself was considered and declined — the catalog is tenant-authored domain
  data and the tenant owns its correctness (architecture doc §4.2 puts adopter
  config validation in the adopter's CI). The trace event on every resolution
  is the only breadcrumb when it goes wrong.
- **A scheme the classifier does not recognise at all stays unresolved.** For
  a bare "PKVY?" the classifier may return no asks, and enrichment never
  authors an ask — it refines what was found. If this proves to matter,
  option 3 narrowed to the 13 canonical *names* (~200 tokens, no extra round
  trip) is the intended next step, not a tool call.
- Misspellings ("makna") are handled by a `difflib` similarity fallback
  (#37), tried only after every exact lookup has missed and only against an
  ask's own extracted subject — never the raw query, where a long sentence
  scores meaninglessly against a two-word alias. `DSS_SCHEME_FUZZY_THRESHOLD`
  configures it and `None` turns it off; the floor is above 0, which would
  otherwise match anything against anything. A match records whether it was
  exact or fuzzy, because a wrong fuzzy hit is the failure this can produce.
- Normalization lives in `core/` and is imported by the adapter, so the index
  and the lookup cannot drift. Its tokenizer is a character scan rather than a
  `\W` regex split, because Unicode marks are not word characters and a
  Devanagari query would otherwise be shredded.
