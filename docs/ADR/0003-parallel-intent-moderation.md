# ADR-0003: Intent and moderation run in parallel, decoupled, over the raw query

- **Status:** ACCEPTED
- **Date:** 2026-09-02
- **Deciders:** DSS code owners
- **Consulted:** Product Owner
- **Informed:** Adopter engineering teams

---

## 1. Context and Problem Statement

The moderation slice (spec 0004) modelled `Intent` as a *root* of the
`ModerationContext`: a policy could address `turn.*` and `intent.*`, which implied
intent classification runs **before** moderation and feeds it. In practice the two
shipped policies (profanity filter, delete-command) read only the turn, so the
ordering bought nothing but latency — two LLM round trips in series.

Two further needs pull in the same direction:

- **Follow-ups.** A turn like *"And potato?"* or *"Is it safe to use?"* is only
  meaningful against the prior turn. Moderation must judge it without mistaking a
  terse, reference-bearing question for gibberish or an attack.
- **Empathy.** When a user swears (*"why won't you just answer, this is shit"*),
  the profanity filter already strips the word — but the reply that follows reads
  as a scold, not an acknowledgement that they are frustrated.

The question: **how should intent and moderation compose, what query does
moderation judge, and how is frustration surfaced?**

## 2. Decision Drivers

1. **Latency.** Intent and moderation gate the same turn; running them serially
   doubles the wait for no dependency that actually exists.
2. **Judge what the user said.** Moderation deciding on an enriched/rewritten query
   risks acting on words the user never typed.
3. **Resolve references without a rewrite step.** Follow-ups must be understood,
   but adding a coreference-rewrite stage in front of moderation reintroduces the
   coupling (and the "judge a rewrite" risk) we are removing.
4. **Keep enforcement structural** (per ADR-0002): a rejected turn still carries no
   partial answer.

## 3. Considered Options

- **Option A — parallel + decoupled; raw query; history as LLM context; empathy as
  a deterministic flag.** Run `classify_intent()` and `moderate()` under
  `asyncio.gather`. Drop `intent` from `ModerationContext`. Moderation reads
  `turn.original_query` and passes `turn.history` into the LLM prompt for reference
  resolution. Stripping profanity sets `frustration_detected` on the decision.
- **Option B — keep intent → moderation serial, add a coreference-rewrite step.**
  Rewrite "And potato?" to a standalone query, then classify and moderate that.
- **Option C — an LLM sentiment classifier for empathy.** A dedicated per-turn call
  decides frustration independently of profanity.

## 4. Decision Outcome

**Chosen option: A.**

- **Parallel + decoupled, moderation gates.** `orchestration/turn.py::run_turn`
  gathers the two core calls and returns a `TurnResult(intent, decision)`.
  `ModerationContext` loses its `intent` field; a policy addresses only `turn.*`.
  The two run concurrently for latency, but **moderation still gates the result**:
  on any non-`PROCEED` outcome the classified intent is discarded in favour of an
  empty `Intent()`, so a turn the assistant refuses to act on never surfaces an
  intent read off that same text.
- **Raw query.** `moderate()` reads `context.turn.original_query`. The deterministic
  word-check runs on that raw text; the (possibly sanitized) result is what the LLM
  policies judge.
- **History as context, not a rewrite.** `build_llm_prompt(policies, history)`
  renders the recent thread into the prompt "for reference only"; `user_query`
  stays the literal raw query. The same window is given to `classify_intent`. The
  model resolves the reference itself — no separate rewrite stage, so moderation
  never judges text the user did not send.
- **Empathy is deterministic.** A stripped profanity word is read as frustration:
  `ModerationDecision.frustration_detected` is set (only valid on `PROCEED`, same
  validator family as `sanitized_query`), and `messages_for` leads with an
  empathetic acknowledgement before the sanitization warning.

### 4.1 Positive Consequences

- Turn latency is the slower of the two calls, not their sum.
- Moderation is honest: it judges what the user typed, with context but without
  rewriting.
- No new machinery for empathy — one boolean, rendered by the existing message
  layer.

### 4.2 Negative Consequences

- Frustration detection is coarse: only banned-word turns trip it, so a polite-but-
  frustrated user is not acknowledged (see revisit triggers → Option C).
- `run_turn` computes the intent classification even when moderation rejects and
  then throws it away. Accepted: the calls are concurrent, so the wasted call costs
  no wall-clock; blanking it in the coordinator is what keeps a refused turn from
  leaking an intent.

## 5. Rejection Rationale

**Option B (serial + rewrite)** keeps the latency it set out to remove and adds a
rewrite step whose output moderation would then judge — exactly the "act on words
the user never said" risk driver 2 rejects.

**Option C (LLM sentiment)** adds a third per-turn LLM call for a signal the
deterministic profanity strip already provides in the cases that matter now.
Deferred, not refused — the trigger below names when to take it.

## 6. Revisit Triggers

- **Toward Option C:** frustration needs to be caught without profanity (e.g. an
  angry but clean message), or empathy needs to modulate tone beyond a fixed lead
  line.
- **Toward re-coupling:** a future policy genuinely needs the intent classification
  to decide harm (then intent returns to the context — but measure the added
  latency first).

## 7. Follow-up Actions

- **[DSS code owners]** When response composition lands, decide whether
  `frustration_detected` also softens the *answer's* tone, not just the prepended
  message.
- **[DSS code owners]** Reflected in `DSS_ARCHITECTURE.md` §3 (logical functions and
  control flow) in this change.

## 8. Notes

- Builds on ADR-0002 (redact-and-warn as a `PROCEED`-carried transform); reuses the
  same "only-on-PROCEED" validator technique for `frustration_detected`.
- The coordinator uses plain `asyncio.gather` rather than a pydantic-graph: the
  control flow is a two-way fan-out with no shared state, so the framework buys
  nothing yet. Revisit if the turn grows conditional stages.
