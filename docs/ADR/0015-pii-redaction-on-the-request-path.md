# ADR-0015: PII redaction on the request path

- **Status:** PROPOSED
- **Date:** 2026-09-30
- **Deciders:** DSS code owners
- **Informed:** Adopter engineering teams; OAN DPG steward
- **Issue:** OpenAgriNet/engineering-tracker#133

---

## 1. Context and Problem Statement

A farmer's question goes to an AI model, to the trace store and to the audit
file. Nothing is removed on the way. An Aadhaar number typed into a question
reaches the model provider, sits in Langfuse and stays in `turns.jsonl`.

The sink step in `DSS_ARCHITECTURE.md` §6.2 (part-built by ADR-0014) deletes
whole fields before a write. It does not look inside text, and it does nothing
for the model call.

Some providers need a farmer's phone number to answer. So the question is not
only what to remove, but where a value may go.

## 2. Decision Drivers

- **Before the model call**, not only before a write.
- **One pass**, so the model, traces and audit record see the same text.
- **No false matches on farming words.** Kamal, Tulsi and Sona are names and
  crops. Breaking "champa ka rate" is worse than missing a name.
- **Configuration, not code**, so an adopter adds an identifier without a release.
- **Fast** (within 10ms) and **testable at tier 1**.

## 3. Considered Options

**Where it runs.**

- **A. At each sink.** Rejected: the model call is not a sink.
- **B. In moderation.** Rejected: moderation runs beside intent, so intent would
  still see raw text.
- **C. Once, in the orchestrator, before anything else.** Chosen.

**How it finds identifiers.**

- **A. Patterns plus checksums** (Verhoeff, Luhn, GSTIN; strict formats for PAN,
  IFSC, phone, email). Chosen: a false match is very unlikely.
- **B. A named-entity model.** Not now: a model dependency, a per-language
  question, and weak on romanised Hinglish. It can be added later as another
  identifier.
- **C. A list of names.** Rejected: names collide with crop words.

Python's `re` over `google-re2`: RE2 has no lookarounds, which the rules need.

## 4. Decision Outcome

**Redaction runs once, in the orchestrator, before the audit write and before
intent, moderation and discovery.** It covers the question, every history
message and the user's phone, if the turn carries one. Every later stage sees
only tags such as `«phone_1»`.

**Finding PII is a port; redacting it is core.** Identifiers (`regex` today)
sit behind `ports/pii_identifier.py` and only report spans. Core settles
overlaps, numbers the tags and applies the policy. So no identifier, even a
remote one, can make an Aadhaar kept. Identifiers run side by side; one that
fails is dropped for that turn and the others still apply. Detail:
`DSS_ARCHITECTURE.md` §6.2.

**Everything is configuration.** The rules file lists the identifiers and, per
entity, whether the value is kept or destroyed. A bad entry stops the boot and
names itself. Off unless `DSS_REDACTION_ENABLED=true`; a ready file for India
ships at `src/dss/config/examples/redaction-rules.yaml`.

**Kept values reach two places only.** The map from tag to real value lives for
one turn. The `/select` call swaps the tag for the value on the wire, and an
echoed value back to its tag. The farmer's own answer gets the value back as it
leaves the DSS; the audit record and traces keep the tag. Without a map, a call
sends the tag, so forgetting one fails safe.

**Aadhaar and card are destroyed by default.** They are never held, so no
provider can be sent them (Aadhaar Act; PCI). To be confirmed with counsel.

**Names only when announced** ("my name is ..."), English only for now.

## 5. Consequences

**Good**

- The model, traces and audit file no longer see the listed identifiers.
- A provider that needs a farmer's phone still gets it.
- A new identifier is an adapter plus a config entry; core does not change.

**Accepted for now**

- **The planner decides who gets a kept value.** No schema pack declares which
  fields may receive one.
- **If redaction fails, the turn continues unredacted.** It is counted and
  logged. Failing closed can be added later.
- **A slow pattern is not rejected at boot** (ReDoS).
- **A value lives for one turn**, so a farmer repeats it on the next.
- **Only the listed shapes are caught**, and old traces are not cleaned.
- **Langfuse still gets prompts verbatim** (ADR-0007 §5), now redacted.
