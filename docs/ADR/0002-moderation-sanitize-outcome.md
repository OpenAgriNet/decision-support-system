# ADR-0002: Redact-and-warn as a PROCEED-carried transform, not a new outcome

- **Status:** ACCEPTED
- **Date:** 2026-08-26
- **Deciders:** DSS code owners
- **Consulted:** Product Owner
- **Informed:** Adopter engineering teams

---

## 1. Context and Problem Statement

Spec 0004 fixes moderation's verdict as one of four outcomes — `PROCEED`,
`REJECT`, `CLARIFY`, `NO_MATCH` — and states a load-bearing rule: **harm never
partitions.** Once a harm policy fires, the whole turn is refused; there is no
partial answer, no sanitization, no "answer the safe part."

The first policy pack this repo ships needs a behaviour that rule does not cover.
A **profanity filter** should not refuse the turn: given *"I want to know potato
price. You are a shit chatbot"*, the desired behaviour is to **strip the
profanity, warn the user, and let the real question proceed** — not to reject a
farmer's legitimate question because it carried an insult.

This is not harm partitioning (answering half of a harmful request). It is a
*transform* on a benign-but-impolite query that leaves nothing unsafe behind. But
the four-outcome type cannot express it: there is no field on `ModerationDecision`
that carries "here is the cleaned query, and here is what I changed."

The question: **how should redact-and-warn be modelled?**

## 2. Decision Drivers

1. **Do not weaken "harm never partitions."** Whatever is added must keep a
   rejected turn incapable of carrying a partial/cleaned answer — structurally,
   not by convention.
2. **Keep enforcement structural.** The caller branches on `outcome`; a new
   behaviour must not require the caller to learn a new control-flow shape.
3. **Minimal surface.** Only two policies exist; do not build a general policy
   "action" framework before a second case needs it.
4. **Reversibility.** The chosen shape should not need re-threading through every
   producer/consumer when response composition later renders the warning.

## 3. Considered Options

- **Option A — `PROCEED` carrying `sanitized_query` + `warnings`.** The decision
  outcome stays `PROCEED`; two new optional fields carry the cleaned query and the
  user-facing warnings. A validator forbids them on any non-`PROCEED` outcome.
- **Option B — a new `on_violation: redact` policy action.** Add redaction as a
  first-class policy action alongside `reject`/`clarify`, with a replacement rule
  in the policy schema, so any future word list reuses it generically.
- **Option C — reject on the banned word.** Treat profanity like any harm policy.

## 4. Decision Outcome

**Chosen option: A — `PROCEED` carrying `sanitized_query` + `warnings`.**

`ModerationDecision` gains:

```python
sanitized_query: str | None = None
warnings: list[str] = Field(default_factory=list)
```

and a validator that raises if either is set when `outcome is not PROCEED`.

This keeps "harm never partitions" **structural**: a `REJECT` literally cannot be
constructed with a `sanitized_query`, so a rejected turn carrying a half-answer is
unconstructable — the same technique 0004 uses for its `unsupported` field. The
caller's control flow is unchanged: it already branches on `outcome`, and a
redacted turn is simply a `PROCEED` whose `sanitized_query` (when present) is the
text to feed downstream.

Redaction runs in the deterministic phase and is **non-terminal**: the cleaned
query flows on to the LLM policies, so the delete-command check judges the
sanitized text, and a turn that is both impolite and malicious is still rejected.

### 4.1 Positive Consequences

- The harm-never-partitions invariant is enforced by a type validator, not a code
  path anyone has to remember.
- No new outcome, no new caller branch, no new policy-action machinery.
- `warnings` is exactly what response composition will stream later; when that
  slice lands it reads an existing field rather than forcing a shape change.

### 4.2 Negative Consequences

- `ModerationDecision` carries two fields only the profanity path populates today.
  Accepted: they are optional and inert elsewhere.
- Redaction behaviour is currently specific to `WordCheckPolicy` rather than a
  general policy action. If a second, differently-shaped redaction need appears,
  revisit toward Option B (see triggers).

## 5. Rejection Rationale

**Option B (a `redact` policy action)** generalises before there is a second case,
and it puts the sanitized-query/warnings plumbing on `ModerationDecision`
*anyway* — so it is Option A plus a schema change the current pack does not
exercise. Deferred, not refused: the revisit trigger below names when to take it.

**Option C (reject on the word)** contradicts the requirement outright — it throws
away the farmer's real question over an insult, which is the exact "refuse a
legitimate question" failure the four-outcome design elsewhere works to avoid.

## 6. Revisit Triggers

Reopen toward Option B when **any** of these becomes true:

1. A second redaction need appears whose shape is not "strip whole words from the
   query" (e.g. masking an entity, redacting a span in a tool result).
2. A redaction is wanted at a checkpoint other than moderation, where
   `WordCheckPolicy` does not apply.
3. Adopters need to author redaction rules generically in YAML beyond a word list.

## 7. Follow-up Actions

- **[DSS code owners]** When response composition lands, render `warnings` into the
  streamed answer (0004 already flags the symmetric `unsupported` renderer gap).
- **[DSS code owners]** Fold this decision type note into `DSS_ARCHITECTURE.md` when
  the moderation decision contract is promoted from spec 0004 into the architecture
  doc.

## 8. Notes

- Anchored on spec `docs/.agent/plan/0004-moderation-service.md` (the four-outcome
  contract and "harm never partitions") and 0003 (the policy schema).
- This ADR does not change the LLM-evaluated path, the barrier, or the failure
  model — only how a benign redaction is represented in the verdict.
