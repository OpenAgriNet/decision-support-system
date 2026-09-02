# 0002 — Intent recognition

**Status:** in review

## Problem

Intent recognition is the first logical function of a turn (§3, execution order).
It turns a farmer's sentence into labels the rest of the pipeline reads, so
nothing after it — moderation, routing, planning — can be built until the intent
object exists and something produces it. Before this change `core/intent` held
only a docstring.

## Scope

**In:**
- `core/intent/models.py` — `ActionType`, `Ask`, `Intent`, `Taxonomy`, and the
  shipped `BASE_TAXONOMY` (`Crop, Livestock, Weather, Market, Scheme, Knowledge,
  Service`).
- `core/intent/service.py` — `classify(turn, taxonomy, ctx) -> Intent`.
- `core/shared/` — the `UserTurn` envelope (from the API contract §2) and
  `TurnContext` (per-turn dependencies), since `classify` consumes both and
  neither existed.
- `ports/llm.py` — the `LLMProvider` seam plus `LLMError` / `LLMTimeoutError`.
- `adapters/llm/openai_provider.py` — a concrete provider over the OpenAI SDK,
  default model `gpt-5.6-luna` ("ChatGPT 5.6 Luna"), overridable via `LLM_MODEL`.
- Tier-1 tests for the service and models; a tier-2 contract test for the
  adapter with a stubbed client.

**Out, deliberately:**
- Orchestration wiring (`orchestration/` calling `classify`) — the next slice;
  keeps this reviewable as a core function plus its port.
- Layers 1–4 of §5.2's layered extraction (session cache, frequency cache,
  regex, embeddings). This is the layer-5 LLM classifier only.
- Entity resolution and reference resolution against history — intent labels
  only; enrichment is a separate function (§3, §8.3 open item #7).
- Moderation of the query (runs after intent, §3.0).

## Decisions

### The intent is a tuple of asks, not one domain

A single sentence routinely holds several questions. Modelling one
`primary_domain` per turn forces the classifier to pick one and lose the rest.
`Intent.asks: tuple[Ask, ...]` — one ask per question, one ask → one plan step —
carries them all. This replaces the directional sketch in §5.2 (updated in the
same change). Empty asks is a valid `Intent` ("understood nothing"), not an
error; confidence is the model's own and independent of ask count.

### Domain rules live in core, not in the prompt

The LLM is asked to label, but core re-checks everything it returns: a category
outside the taxonomy is dropped (never coerced), a malformed `action_type` drops
that ask (never guessed), a blank subject normalises to `None`. The model's
output is untrusted; a garbled item degrades to a dropped ask rather than an
exception. This keeps the rules in tier-1 tests, not in prompt wording that only
a tier-4/5 test could pin.

### A timeout raises; it is never a fabricated intent

`LLMTimeoutError` is a distinct port exception and propagates out of `classify`.
"Understood nothing" (empty asks) and "failed to get an answer" (timeout) are
different outcomes and the orchestration layer maps them differently (§7).

### Provider neutrality via a port; the adapter uses the raw SDK

`classify` depends on `dss.ports.LLMProvider`, injected through `TurnContext`, so
core imports no vendor SDK and stays behind the framework boundary (ADR-0001
§4.3). The OpenAI adapter talks to the SDK directly, **not** through Pydantic AI
— the framework is confined to `orchestration/`. `--import-mode=importlib` was
added to the pytest config so the src-mirrored suite can reuse basenames.

## Verification

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest -q      # 60 pass (tier 6 excluded)
```

Tier-1 covers the worked example (3 asks), out-of-taxonomy drop, empty asks,
malformed action_type drop, case-insensitive resolution, confidence clamping,
and timeout propagation. Tier-2 covers the adapter's JSON parsing, structured-
output selection, and timeout/error mapping against a stubbed client.

## Follow-ups

- Wire `classify` into `orchestration/` behind the moderation checkpoint.
- Layers 1–4 of the layered extraction (§5.2), instrumented for hit-rate.
- On merge: delete this plan; the durable design already lives in §5.2.
