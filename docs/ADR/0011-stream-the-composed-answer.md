# ADR-0011: Stream the composed answer on its own route

- **Status:** ACCEPTED
- **Date:** 2026-09-21
- **Deciders:** DSS implementation
- **Consulted:** —
- **Informed:** Experience-layer engineering leads

---

## 1. Context and Problem Statement

A farmer sees nothing until the whole answer is ready. The DSS already streams on
the wire — ADR-0002 fixes SSE as the streaming transport, `sse.py` frames events,
and `Orchestrator.run` is an async generator — but nothing it streams is
incremental. The composer was `async def compose(...) -> str`, one blocking model
call, and `answer_from_evidence` wrapped that whole string in a single
`TextBlock`. A turn therefore emitted exactly one `claim.completed`, produced
only after composition finished, immediately followed by `turn.completed`.

The composer is the last stage of a turn (intent ∥ moderation → discovery →
planner → composer) and the only stage whose output exists before its work is
done. Every word it has written is held back until the last one is. Releasing
them is the largest perceived-latency change available without altering how the
DSS reasons or which providers it calls.

Three things had to be decided to do it.

**Where the streaming call lives.** `LLMProvider` offered `structured()` only —
prose is not a schema — so the composer sat in `orchestration/` calling Pydantic
AI directly, which its own docstring flagged as temporary.

**Whether the whole-answer path survives.** A caller sending
`Accept: application/json` gets one body. Draining a stream to build it is
possible; keeping a separate call is also possible, and they differ on retries.

**How a runner learns which one to use.** This is the hard one. `ports/turn.py`
states the invariant plainly: *"Streaming never crosses this seam. The runner
produces events; whether they become SSE frames or a single JSON body is the
transport's business."* The router knows the mode; the orchestrator is never
told it.

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
  asks for SSE.
- **B. One route, a flag through the seam.** Add `stream: bool` to
  `TurnRunner.run` (or to `TurnContext`) so the orchestrator picks a composer.
- **C. A second route, `POST /v1/stream/turns`,** bound to a runner already wired
  to stream.

### Composer seam

- **D. Keep the composer in `orchestration/`** calling Pydantic AI directly, and
  add streaming there.
- **E. Add `stream_text()` (and `text()`) to `LLMProvider`** and move composition
  into `core/`.

## 4. Decision Outcome

**C and E.**

A new component, `core/stream_response/`, yields the answer in the pieces the
model writes it in. `core/channel/compose.py` — the whole-answer composer, moved
out of `orchestration/` — keeps today's behaviour. Both build their prompt from
`core/channel/prompt.py`, so they cannot drift into asking the model different
questions. Both reach the model through `LLMProvider`, which gains `text()` and
`stream_text()`.

`Components.compose_stream` is how a runner is told to stream. The composition
root builds two `Orchestrator`s over shared components, sinks and model
bindings; `build_app` mounts `/v1/turns` on the first and `/v1/stream/turns` on
the second.

**Why not A.** `Accept` already selects framing on `/v1/turns`, and this would
make it silently select *content* as well: the same route would start emitting a
frame type existing consumers have never seen. It also gives no way to offer
streaming to one adopter while another is still on the old shape.

**Why not B.** It contradicts the invariant in §1 directly. The mode would then
reach `core/` through the runner, and every test of the orchestrator would need
to say which transport it was pretending to be. The route-picks-the-runner
arrangement gets the same result with the seam intact: the runner is *configured*
to stream, never *told* to.

**Why not D.** The composer would have stayed the only thing in `orchestration/`
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
- **No retry once a piece is out.** `stream_text` offers none. A second attempt
  would write a *different* answer (the composer runs at temperature 0.3, with no
  seed) over words the farmer has already read, and sent bytes cannot be
  recalled — there is no `id:` line and no reset frame in the contract. A failure
  after the first delta becomes a terminal `turn.failed` inside the already-open
  200, which is the path `router._frames` already implements. Retries stay on
  `text()`, where nothing has reached the caller yet, and on the streaming path
  they are simply unavailable.
- **Deltas are debounced.** Every frame repeats the whole response envelope, so
  one frame per token is mostly envelope. `PydanticAILLMProvider` groups 100ms of
  pieces by default, costing the farmer at most 100ms on the first word.

## 5. Consequences

**Good.**

- First words reach the farmer while the rest is still being written.
- The composer no longer imports a vendor SDK; a framework swap does not reach
  the prose.
- `/v1/turns` is byte-for-byte unchanged, so no adopter is forced to move.
- Streaming is removable: delete the route, the component and the wiring.

**Costs accepted.**

- **Two composers.** One prompt, two calls, and a tier-1 test asserting both send
  the model the same thing. That test is the only thing standing between this and
  two answers to the same question.
- **Two routes to document and keep in step.** They share admission
  (`router._admit`) and framing (`sse.Stream`), so the duplication is the route
  declaration and its OpenAPI block.
- **A transport failure after the first token now surfaces** to the farmer rather
  than being retried away. The window is short, and showing a farmer one price
  and then replacing it with another is worse.
- **A new wire content type**, `output_text_delta`. Additive, and consumers that
  ignore unrecognised event names are unaffected — *to be confirmed with the
  Experience layer before release*.

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
