# ADR-0011: Stream the composed answer

- **Status:** ACCEPTED
- **Date:** 2026-09-21
- **Deciders:** DSS implementation
- **Consulted:** —

---

## 1. Context and Problem Statement

A farmer sees nothing until the whole answer is ready. The DSS already streams on
the wire — ADR-0002 fixes SSE as the streaming transport, `sse.py` frames events,
and `Orchestrator.run` is an async generator — but nothing it streams is
incremental. The composer was `async def compose(...) -> str`, one blocking model
call, and `answer_from_evidence` wrapped that whole string in a single
`TextBlock`. A turn therefore emitted exactly one `claim.completed`, produced
only after composition finished, immediately followed by `turn.completed`.

The composer is the last stage of a turn *today* (intent ∥ moderation →
discovery → planner → composer), and the only stage whose output exists before
its work is done. Every word it has written is held back until the last one is.
Releasing them is the largest perceived-latency change available without
altering how the DSS reasons or which providers it calls.

**It will not stay last.** An optional Response Reviewer and a Channel Response
component come after it (`dss-design-v2.md` §9, §11). Neither changes this
decision, but both have to respect it: a reviewer that must read the whole
answer before the first word leaves gives back everything streaming bought,
which is why design-v2 already scopes review as non-blocking, and a channel
shaper that rewrites the finished text would break the guarantee that the pieces
sent equal the block that follows. Whatever lands after the composer either
works piece-by-piece or runs alongside the stream rather than in front of it.

Three things had to be decided to do it.

**Where the streaming call lives.** `LLMProvider` offered `structured()` only —
prose is not a schema — so the composer sat in `orchestration/` calling Pydantic
AI directly, which its own docstring flagged as temporary.

**Whether a whole-answer path survives.** A caller sending
`Accept: application/json` gets one body. Draining a stream to build it is
possible; keeping a separate model call is also possible, and they differ on
retries.

**Whether streaming gets its own route.** `ports/turn.py` states an invariant
plainly: *"Streaming never crosses this seam. The runner produces events;
whether they become SSE frames or a single JSON body is the transport's
business."* The router knows the mode; the orchestrator is never told it. So a
runner cannot *choose* a composer per request — which, if two composers existed,
would force either a second route or a change to that invariant.

## 2. Decision Drivers

1. **Time to first word**, which is the entire point.
2. **The finished answer must not change** — same text, same sources, same
   outcome, whichever way it was delivered.
3. **`ports/turn.py`'s invariant is load-bearing**, not incidental: it is why the
   runner is testable without a transport and why the transport is testable
   without a pipeline.
4. **Existing integrations keep working**, including any that never adopt this.
5. **Two composers must not become two prompts.**
6. **Reversibility** — this is a first cut, and removing it should be deleting a
   route and a component, not unpicking the pipeline.

## 3. Considered Options

### Interface

- **A. One route, `Accept` selects.** `/v1/turns` streams deltas when the caller
  asks for SSE, and drains them into one body when it asks for JSON.
- **B. One route, a flag through the seam.** Add `stream: bool` to
  `TurnRunner.run` (or to `TurnContext`) so the orchestrator picks a composer.
- **C. A second route, `POST /v1/stream/turns`,** bound to a runner already wired
  to stream.

### Composers

- **D. Two composers** — a streaming one and a whole-answer one, sharing a
  prompt. Only the second may be retried.
- **E. One composer, which streams.** A whole-answer caller joins the pieces.

### Composer seam

- **F. Keep the composer in `orchestration/`** calling Pydantic AI directly, and
  add streaming there.
- **G. Add `stream_text()` to `LLMProvider`** and move composition into `core/`.

## 4. Decision Outcome

**A, E and G.**

One component, `core/stream_response/`, yields the answer in the pieces the model
writes it in. It is the only composer: a caller asking for
`Accept: application/json` gets the pieces drained into the same single body as
before, which the transport was already doing for claims. `LLMProvider` gains
`stream_text()` and nothing else.

**A and E stand or fall together.** With one composer there is nothing for a
runner to choose between, so the invariant in §1 is satisfied without a second
route: the runner always streams, and `Accept` decides only whether the pieces
are framed or drained. Two composers would have forced C (or B, which
contradicts the invariant outright) — and two composers is what E declines.

**Why not B.** It contradicts the invariant in §1 directly. The mode would reach
`core/` through the runner, and every orchestrator test would have to say which
transport it was pretending to be.

**Why not C.** It was the first cut of this ADR, and review rejected it: a second
route is a second contract, a second OpenAPI block and a second thing every
adopter has to learn, to express what one header already expresses. The reasons
originally given for it do not survive E — with a single composer there is no
per-request choice to make, so the seam argument that motivated C no longer
applies.

**Why not D.** Two calls over one prompt is two chances to answer the same
question differently, guarded only by a test. The retry that justified the second
composer is worth less than it looks: retries protect against failures before the
first token, and those are equally invisible to a streaming caller who has not
received a piece yet. What is genuinely lost is a retry *after* the first token
for a JSON caller — see Consequences.

**Why not F.** The composer would have stayed the only thing in `orchestration/`
that exists because a port was missing rather than because it needs the
framework. Moving it is the change ADR-0001's layering already implied.

### Streaming rules, and why

- **Pieces pass through untouched.** Nothing re-splits them onto word or sentence
  boundaries. The guarantee everything downstream rests on is that concatenating
  the deltas gives the completed claim exactly; a tidier boundary is bought by
  giving that up.
- **A delta carries no citations.** `mapping._annotations` spans a whole block —
  `startIndex 0`, `endIndex len(text)`. Mid-write there is no end index, and a
  citation over a moving target is worse than none. Provenance arrives with
  `claim.completed`.
- **No retry once a piece is out.** `stream_text` offers none, and there is no
  other way to compose. A second attempt would write a *different* answer (the
  composer runs at temperature 0.3, with no seed) over words the farmer has
  already read, and sent bytes cannot be recalled — there is no `id:` line and no
  reset frame in the contract. A failure after the first delta becomes a terminal
  `turn.failed` inside the already-open 200, which is the path `router._frames`
  already implements.
- **Closing the stream closes the model call.** `async for` is not `yield from`:
  closing an async generator does not reach the one it is relaying from, so a
  farmer who hangs up mid-answer would otherwise leave the model's HTTP
  connection open until the garbage collector finalised it. `contextlib.aclosing`
  at both relay points — the composer and the runner — with a regression test at
  each.
- **Deltas are debounced.** Every frame repeats the whole response envelope, so
  one frame per token is mostly envelope. `PydanticAILLMProvider` groups 100ms of
  pieces by default, costing the farmer at most 100ms on the first word.

## 5. Consequences

**Good.**

- First words reach the farmer while the rest is still being written.
- The composer no longer imports a vendor SDK; a framework swap does not reach
  the prose.
- One route, one composer, one prompt: there is no arrangement in which two
  callers get differently-worded answers to the same question.
- A JSON caller's request and response are unchanged.

**Costs accepted.**

- **An SSE caller on `/v1/turns` now receives a frame type it has not seen
  before.** `claim.delta` is additive and `claim.completed` still carries the
  whole block, so a consumer that ignores unrecognised event names is unaffected
  — *to be confirmed with the Experience layer before release*. If any consumer
  errors on an unknown event instead, this is a versioned contract change.
- **A transport failure after the first token now surfaces** to the farmer rather
  than being retried away, and this now applies to JSON callers too, who
  previously had a retry they could not observe. The window is short — retryable
  composer failures (connection setup, auth, rate limits, cold starts) land
  before the first token, where nothing has been sent and the failure still
  propagates as it always did. Showing a farmer one price and then replacing it
  with another is worse than the alternative.

**Measured.** The saving is smaller than the motivation suggests: over five
runs against a real model, the time between the first piece and the finished
turn is **0.27s median on a ~13.6s turn**. The composer span is ~4s, of which
~3.8s is time-to-first-token — which streaming cannot help — and ~0.2s is the
writing it releases early. The planner is 60% of the turn. The feature is
correct and worth keeping, but it is not where a latency budget should be spent
next.

**Still open.**

- **Resumability.** Unchanged: no `id:`, no `Last-Event-ID`. A dropped connection
  still means the DSS finishes the turn server-side and the caller recovers it
  from session history (design-v2 Open #12).
- **Answer length caps.** `response_max_chars` is carried on `UserTurn` and read
  by nothing, before this change or after it.
- **Translation.** If translation is ever added as a step *after* composition,
  this feature stops working for every non-English farmer: the whole English
  answer would have to exist before translation could start. Translation must
  remain part of generation — the composer already writes in `target_lang`.
