# ADR-0021: The Intent Model Names the Scheme

- **Status:** ACCEPTED
- **Date:** 2026-10-29
- **Deciders:** DSS implementation
- **Supersedes:** [ADR-0008](0008-intent-time-scheme-resolution.md)

---

## 1. Context and Problem Statement

A grievance or status ask needs to know which scheme it is about. The form
step picks a form by the scheme's code (#205, #206). Today a separate
enrichment step finds the scheme after intent: it matches the farmer's words
against an alias list from the scheme catalog CSV (ADR-0008).

That step only sees what the intent model already wrote down. It cannot
tell "my crop insurance claim" is PMFBY unless an alias says so word for
word. It is a second pass over the same question, with its own tokenizer,
fuzzy threshold and trace stage to keep working.

The intent model already reads the whole query and the recent chat. It is
better placed to say which scheme the farmer means, if it is shown the list.

## 2. Decision Drivers

1. A farmer's way of naming a scheme should not need an exact alias.
2. One step should decide what the ask is about, not two.
3. The scheme code must still come from the catalog, never the model's
   memory.
4. The catalog stays a CSV an agriculture officer can edit.

## 3. Considered Options

1. **Keep enrichment** (ADR-0008): alias lookup after intent.
2. **Show the catalog to the intent model** and let it return the scheme's
   code on each ask. Drop enrichment.
3. **Let the intent model call a lookup tool.** Rejected in ADR-0008 for
   cost and latency; nothing has changed that.

## 4. Decision Outcome

Chosen option: **2.**

- The catalog CSV is read at boot as before (`DSS_SCHEMES_CONFIG_PATH`).
  Each row — code, name, aliases — goes into the intent prompt.
- Each classified ask returns a `scheme_code`, or null.
- The DSS keeps the code only if the catalog has it. A code the catalog does
  not hold is dropped, so the model cannot invent a scheme.
- A kept code puts the catalog's `Scheme` on the ask, and the ask's subject
  becomes the scheme's official name, as enrichment did.
- The model picks the category itself. Nothing overrides it afterwards.
- `core/enrichment/`, its trace stage and `DSS_SCHEME_FUZZY_THRESHOLD` are
  removed.
- For now the catalog is `configs/schemes.csv`, a copy of the test fixture.
  It ships in the image, and compose and `run-local.sh` point at it by
  default. A deployment replaces it by setting `DSS_SCHEMES_CONFIG_PATH`.

## 5. Consequences

- The intent prompt grows with the catalog: about one line per scheme. Fine
  at today's size. A catalog of hundreds of schemes would need a rethink.
- Matching is now the model's judgement, not a lookup. A wrong match is
  possible and only an eval will show how often. The trace shows the
  `scheme_code` on each ask.
- A misspelling or a loose description ("crop insurance") can now match,
  which the alias lookup missed.
- With no catalog mounted the prompt says there are no schemes, and no ask
  gets one — the same as before.
- The duplicate-alias check in the CSV loader stays. Two schemes sharing an
  alias is still a catalog mistake, and would confuse the model too.
