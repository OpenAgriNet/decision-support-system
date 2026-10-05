# ADR-0015: PII redaction on the request path

- **Status:** PROPOSED
- **Date:** 2026-09-30
- **Deciders:** DSS code owners
- **Informed:** Adopter engineering teams; OAN DPG steward
- **Issue:** OpenAgriNet/engineering-tracker#133

---

## 1. Context and Problem Statement

A farmer's question goes to an AI model, to the trace store, and to the audit
file. Nothing is removed on the way. If a farmer types an Aadhaar number, it
reaches the model provider, sits in Langfuse, and stays in `turns.jsonl`.

`DSS_ARCHITECTURE.md` §6.2 plans a redaction step at the *sink* — the logger,
the tracer, the audit writer. ADR-0014 built part of it in the collector, but
that step deletes whole fields. It does not look *inside* text, and it does
nothing for the model call, which is not a sink at all.

Some providers need a farmer's phone number to answer ("status of my
application"). So this is not only about removing a value. It is about where a
value is allowed to go.

**The question.** Where does redaction run, how does it find identifiers, and
how does a provider still get a value it needs?

## 2. Decision Drivers

- **Before the model call, not only before a write.** A sink step cannot
  protect the prompt.
- **One pass.** The model, the traces and the audit record must all see the
  same redacted text. Several passes can disagree.
- **No false matches on farming words.** Many Indian given names are also
  crops or varieties (Kamal, Tulsi, Sona). Breaking "champa ka rate" is worse
  than missing a name.
- **Configuration, not code.** An adopter in another country adds an
  identifier without a release.
- **Fast.** Within 10ms of a turn.
- **Testable at tier 1.** No framework, no I/O.

## 3. Considered Options

**Where it runs.**

- **A. At each sink** (§6.2 as written). Rejected for this problem: the model
  call is not a sink, so the prompt would still carry the raw value.
- **B. In moderation.** Moderation already produces a `sanitized_query` that
  nothing reads. Rejected: moderation runs *in parallel* with intent, so intent
  would still see raw text. And moderation is about harm, not identity.
- **C. Once, in the orchestrator, before anything else.** Chosen.

**How it finds identifiers.**

- **A. Patterns plus checksums.** Aadhaar (Verhoeff), card (Luhn), GSTIN
  (mod-36), and strict formats for PAN, IFSC, phone and email. Chosen: each is
  either checkable or strict enough that a false match is very unlikely.
- **B. A named-entity model.** Rejected for this step: it adds a model
  dependency and a per-language question, and does poorly on romanised
  Hinglish. It is #136, and plugs into the seam in §4.
- **C. A list of names.** Rejected: names collide with crop words, and no
  openly licensed Indian name list exists.

**Which regex engine.** Python's `re` was chosen over `google-re2`. RE2 cannot
hang on a bad pattern, but it is a compiled dependency, and it has no
lookarounds — which the default rules use to stop a phone matching inside a
longer number. See §5.

## 4. Decision Outcome

**Redaction runs once, in the orchestrator, before the audit write and before
intent, moderation and discovery.** It covers the question and every history
message (both roles — an assistant can echo a number back). Every stage after
it sees only the redacted text.

**Identifying PII is a port; redacting it is core.** Finding PII can be done
many ways — patterns, a name model, an HTTP PII service. What happens to it once
found is one set of rules. So they are split:

```
rules file ── entities: {aadhaar: destroy, phone: keep, ...}      (policy)
           └─ identifiers: [{type: regex, rules: ...}, ...]       (which adapters)

ports/pii_identifier.py   PiiIdentifier.identify(texts) -> spans per text
   ▲ adapters/pii_identifier/regex/   patterns, validators, declaring phrases
   ▲ (#136) spaCy, ONNX name models;  (later) an HTTP PII service

orchestration/redaction.py   runs every identifier side by side, merges spans
core/redaction/              resolve overlaps → «phone_1» tags → value map
```

- **`core/redaction/`** knows what a PII span is, the keep-or-destroy policy,
  tag numbering, overlap resolution and the value map. It knows nothing about
  regex, models or HTTP. No spans means the text is unchanged — which is also
  what a turn gets when redaction is off, so no stand-in is needed.
- **`ports/pii_identifier.py`** is the seam. A new way of finding PII is a new
  adapter and a new `identifiers:` entry; core and the orchestrator do not
  change. An identifier only reports spans and their values; the policy, in
  core, decides what is kept. So an identifier — even a remote one — can never
  make an Aadhaar kept.
- **The regex identifier** runs each pattern over the text *and* over a copy
  with number gaps joined, so `98765 43210` and `9876543210 2 acre` are both
  caught. Its validators (Verhoeff, Luhn, GSTIN) are part of the adapter.
- **Identifiers run side by side.** The turn waits for the slowest. One that
  fails is dropped for that turn and named in the result; the others still
  apply. Spans are merged in the order the file lists the identifiers, which
  breaks ties between equal overlapping spans.

**Every rule is configuration.** The policy per entity; for the regex
identifier, each rule's pattern and validator by name. An unknown identifier
type, a pattern that does not compile, or an unknown validator stops the boot
and says which.

**It is opt-in.** `DSS_REDACTION_ENABLED=true` turns it on, and then
`DSS_REDACTION_CONFIG_PATH` must point at a rules file. With the flag off,
nothing is redacted. A ready file for India ships at
`src/dss/config/examples/redaction-rules.yaml`, and our own deployments use it.

**Tags are numbered for every entity.** The same value gets the same tag
within a turn. Angle brackets are not used, as they collide with the prompt
markers in `core/planner/markers.py`.

**Kept values reach one place only.** The map from tag to real value is built
each turn and thrown away with it. It is handed to the `/select` call only.
There it swaps any tag it holds for the real value on the wire, and swaps an
echoed value in the provider's reply back to its tag. Request and reply bodies
are logged in their tagged form.

**Aadhaar and card are destroyed by default.** They get a tag and are never
held, so no provider can be sent them. The Aadhaar Act limits who may hold an
Aadhaar number, and card data brings PCI scope. To be confirmed with counsel;
it is a config value, so changing it needs no release.

**Names only when announced** ("my name is ..."), English only for now. A name
mentioned in passing still reaches the model. That is #136.

## 5. Consequences

**Good**

- The model, the trace store and the audit file no longer see the listed
  identifiers.
- A provider that needs a farmer's phone still gets it.
- An adopter adds an identifier by editing YAML.
- #136 adds a name model as another identifier; an HTTP PII service would be
  one more. Neither touches core.

**Known risks and gaps — accepted for now**

- **The planner decides who gets a kept value.** Any tag the planner puts in
  a `/select` body is swapped, whichever provider it is. No schema pack
  declares which fields need personal data. A per-field declaration is the
  future guard.
- **On a runtime failure the turn continues with the raw text.** The failure
  is counted and logged (exception type only). This breaks "nothing
  unredacted is sent or stored" for that turn. The fallback is a hook, so
  failing closed, or a blunt fallback rule, can be added later.
- **A slow pattern is not rejected at boot.** A pattern can compile and still
  hang on some input (ReDoS). A complexity check, or RE2, is deferred.
- **A value lives for one turn.** A farmer who gave a number earlier must give
  it again. Carrying tags across turns is its own story.
- **Only the listed shapes are caught.** An identifier written in an unusual
  way is missed. This is a floor, not a ceiling.
- **Old data stays.** Traces and audit records written before this ships are
  not cleaned.
- **Langfuse still gets prompts verbatim** (ADR-0007 §5). Those prompts are now
  redacted, so the exemption leaks less, but it is not closed.
