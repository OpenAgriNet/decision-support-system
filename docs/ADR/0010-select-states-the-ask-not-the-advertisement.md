# ADR-0010: `/select` States the Ask's Subject Category, Not the Provider's

- **Status:** ACCEPTED
- **Date:** 2026-09-16
- **Deciders:** DSS implementation

---

## 1. Context and Problem Statement

The two network hops of a single ask disagreed about what was being asked.

`/discover` filters on the **ask's** category — `ProviderQuery.subject_category`,
taken from `Ask.subject_categories.value`:

```
$.catalogs[*].resources[*] ? (@.resourceAttributes.subjectCategories[*] == "Scheme")
```

`/select` then sent the **provider's** categories back at it. The discovery
adapter kept whatever a resource advertised
(`observed_categories=tuple(attributes.get("subjectCategories", ()))`), and the
planner wrote that straight into the structural block of `resourceAttributes`:

```python
"subjectCategories": list(capability.observed_categories),
```

Three things follow from that:

- **The call says nothing about the question.** A resource advertising
  `["Crop", "Practice"]` got both echoed back. That is the provider's own
  advertisement restated as if it were the farmer's ask.
- **A scheme ask arrives labelled as a crop question.** A `Scheme` ask is
  discovered on its category alone (ADR-0009), with no `@type` to pin it, so it
  can match a `KnowledgeAdvisory` resource advertising `["Crop"]` — the only
  category the published packs declare. The select then said `Crop`.
- **An empty array is reachable.** `observed_categories` defaults to `()`, so a
  discover response without `subjectCategories` produced
  `"subjectCategories": []`, violating the pack's `minItems: 1` with no guard
  anywhere.

## 2. Decision Drivers

1. One ask, one category: both hops of the same ask should carry the same
   value.
2. The DSS must not state a provider's advertisement as its own input — the
   distinction ADR-0007 draws between an advertised value and the provider
   describing its own content.
3. `subjectCategories` is required with `minItems: 1`; the value sent must
   never be empty.
4. Structural fields stay out of the model's reach — the fix must not become
   another field the planner LLM fills in.

## 3. Considered Options

1. **Echo the advertisement** (status quo).
2. **Send the ask's category alone**, replacing the observed list.
3. **Send the ask's category unioned with whatever the pack's schema requires**
   (`contains: const`, read from `attributes.yaml`).

## 4. Decision Outcome

Chosen option: **2 — `subjectCategories` is `[ask.subject_categories]`.**

`build_resource_attributes` takes a `subject_category` argument; the planner's
`select` tool reads it from `deps.intent.asks[ask_index]`, the same ask
`find_capability` has already matched `ask_index` against. An `Ask` carries
exactly one category, so the wire value is always a one-item list — which
closes the empty-array hole as a side effect, without a guard.

Option 1 is the defect. Option 3 was rejected on purpose: the union only
matters where the ask's category conflicts with a pack's `contains: const`
(MandiPrice requires `Market`, AgricultureFacility requires `Facility`), and the
only path to that conflict is the `Scheme` index-bypass landing on such a pack.
There, the provider rejecting the call is the correct outcome — the resource
really cannot answer the question — and a union would paper over it by sending
a category the ask never had.

`ProviderCapability.observed_categories` stays. It is still what
`_detect_divergence` compares against the index to raise
`CategoryMappingDiverged`, which is an operator alert about a provider's data.
Only its use as a `/select` input goes away.

## 5. Consequences

**Good**

- A scheme ask reaches the provider as `["Scheme"]`, whatever the matched
  resource advertises.
- `/discover` and `/select` for one ask now carry the same category, so a trace
  reads as one question rather than two.
- `"subjectCategories": []` is unreachable.

**Bad / to watch**

- A `Scheme` ask that index-bypasses onto a pack with a conflicting
  `contains: const` now gets rejected by the provider instead of silently
  succeeding under the wrong category. That surfaces as a `SelectFailed`
  `DEFECT` and a `Failure` on the ask — louder, and correct, but it is a
  behaviour change for a path that used to return something.
- The DSS's `SubjectCategory` enum is narrower than the network's (no
  `Practice`), so a resource legitimately serving a category we cannot classify
  is now selected under the nearest one we can.
