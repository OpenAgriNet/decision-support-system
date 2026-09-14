# ADR-0008: Discover a Scheme Ask by Subject Category Alone

- **Status:** ACCEPTED
- **Date:** 2026-09-14
- **Deciders:** DSS implementation

---

## 1. Context and Problem Statement

A scheme ask never reached the network. "How can i apply for PMKMY" classified
correctly, resolved correctly (ADR-0007), and then stopped:

```
intent      → Ask(subject='PMKMY', category=Scheme, interaction=advise) conf=0.99
enrichment  → scheme_resolved alias=pmkmy scheme_code=pm-kmy fuzzy=False
discovery   → enter … exit ok elapsed_ms=0.1     ← no HTTP call
```

`/discover` was never called, so the turn came back `no_match`.

The capability index is built from the `subjectCategories` observed in each
schema pack's **examples** (`core/provider_discovery/index.py`). The four
published packs declare only `Crop`, `Livestock`, `Market` and `Weather`. So
`("Scheme", "Knowledge")` has no entry, `resolve_capability_type` returns `()`,
`_query_for_ask` returns `None`, and the ask is dropped before any request is
built.

`SubjectCategory.SCHEME` was therefore a category the classifier could emit
that nothing could answer — the trap the enum's own comment describes for
`Practice`.

Two facts make this fixable in the DSS:

- **The `/discover` filter matches `subjectCategories`, not `@type`.** The
  jsonpath expression is built from `ProviderQuery.subject_category`; the
  resolved `@type` only names `context.schemaContext`. A query is therefore
  well-formed with no `@type` at all.
- **Providers already serve `Scheme`.** The live network's
  `KNOWLEDGE_ADVISORY` resource declares `Scheme` in its `subjectCategories`.
  The gap is in our index, not in the network.

## 2. Decision Drivers

1. A category the classifier can emit must be answerable, or it should not be
   in the enum.
2. The DSS must not invent an `@type` the network never advertised.
3. Whatever is done must not widen into a blanket "discover anything" rule —
   an unresolved `Crop` ask is our own bug and should stay loud.
4. No wait on an external repo. The packs live in `network-specs`, on another
   team's release cycle.

## 3. Considered Options

1. **Discover a scheme ask on its category alone**, with no `schemaContext`.
2. **Hardcode a `@type`** for `("Scheme", *)` — e.g. `openagrinet:KnowledgeAdvisory`.
3. **Discover on the category whenever the index resolves nothing**, for every
   category.
4. **Wait for a pack** whose examples declare `Scheme`.

## 4. Decision Outcome

Chosen option: **1 — a scheme ask is discovered on its subject category
alone.** `_query_for_ask` builds a `ProviderQuery` with `capabilities=()`, and
the adapter omits `schemaContext` rather than sending it empty.

The request that leaves is the filter and the spatial constraint:

```json
"filters": {
  "type": "jsonpath",
  "expression": "$.catalogs[*].resources[*] ? (@.resourceAttributes.subjectCategories[*] == \"Scheme\")"
}
```

Option 2 asserts a `@type` no pack connects to `Scheme`, which is the DSS
inventing network vocabulary — the same thing ADR-0007 §1 refuses for scheme
codes. Option 3 turns our own index holes silent: every other category has a
pack, so an empty entry there is a defect and `CapabilityUnresolved` should
keep firing. Option 4 leaves a shipped category permanently unanswerable on
another repo's schedule.

The exception is a named set (`_DISCOVERABLE_WITHOUT_CAPABILITY_TYPE`), not a
general fallback, so adding a category to it is a deliberate edit.

`schemaContext` is **omitted** rather than sent as `[]`: the contract accepts
either the filter or `schemaContext`, and an empty array asserts that no schema
applies rather than that none was named.

## 5. Consequences

**Good**

- A scheme ask reaches the network. Verified live: the same turn now comes back
  `answered`, cited to the network's knowledge-advisory provider.
- Nothing changes for any other category, and `CapabilityUnresolved` keeps its
  meaning — our mapping is incomplete.
- The day a pack declares `Scheme`, the resolved `@type` is used and
  `schemaContext` reappears with no code change.

**Bad / to watch**

- A scheme resource comes back with an `@type` the index does not associate
  with `Scheme`, so `CategoryMappingDiverged` fires for it. That is accurate —
  it is the same index hole — but it is noise until a pack lands.
- Discovery failures on a category-only query are reported against the subject
  category rather than a `@type` (`_failed_labels`), because an empty
  capability tuple would report no failure at all.
- The filter is broader than a typed query: it matches every resource
  advertising `Scheme`, whatever pack it belongs to. Ranking still falls to
  config order (no `textSearch`), so a wide `Scheme` catalog is unordered.
