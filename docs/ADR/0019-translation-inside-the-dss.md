# ADR-0019: Translation Inside the DSS

- **Status:** PROPOSED
- **Date:** 2026-10-15
- **Deciders:** DSS implementation (#174)

---

## 1. Context and Problem Statement

Some languages are served poorly by the models the DSS uses. Gujarati is the
first. The prompts can be given a Gujarati version (ADR-0018), but the model
still has to read and write Gujarati well, and for many languages it does not.

For such a language, the better path is to translate the farmer's turn into a
language the models handle well, run the normal pipeline in it, and translate
the answer back.

`DSS_ARCHITECTURE.md` §5.1 said translation is not a DSS job: the Experience
Layer translates before the DSS sees the turn. That does not hold up:

- Redaction (ADR-0015) runs inside the DSS. A translator outside it would see
  the farmer's phone number and other details before they are hidden.
- The DSS streams the answer (ADR-0011) and reveals kept values piece by
  piece. Translation outside would have to rebuild both.
- The language choice is per language and per deployment, which is
  configuration the DSS already owns (ADR-0018).

This ADR moves translation inside the DSS, for the languages a deployment
lists.

## 2. Decision Drivers

1. The translator never sees a farmer's own details: only redaction tags such
   as `«phone_1»`.
2. Languages not listed behave exactly as before, still streaming piece by
   piece.
3. A deployment chooses per language, through configuration: serve it
   directly, or translate it.
4. The pipeline itself does not change. Intent, moderation, the planner and
   the composer never know a turn was translated.
5. The audit record (`turns.jsonl`) holds the farmer's own language.
6. The farmer still gets the answer as a stream.
7. Tags, citation markers (`[1]`), numbers, units and scheme names survive
   translation, both ways.

## 3. Considered Options

1. **Around the DSS** (the old §5.1): the Experience Layer translates. Breaks
   drivers 1 and 6, and puts a per-language choice outside the DSS's config.
2. **Inline in `Orchestrator.run()`**, at each way out. Translate the turn in
   before the pipeline, then translate at each of the four exits (refusal,
   clarification, no provider, composed answer). Tried first, and dropped in
   review: translation spread through the pipeline, and the pipeline had to
   carry the farmer's turn around for the record (breaks driver 4).
3. **Three layers inside the orchestrator** (chosen): the edge, translation,
   and the core.
4. **Each layer as its own `TurnRunner`**, wrapping the next. The cleanest
   shape, but the reveal step is used mid-turn by provider calls and the turn
   trace is shared across layers. Making each layer a full runner is its own
   refactor.

## 4. Decision Outcome

Chosen option: **3, three layers inside the orchestrator.**

```
run()                              edge: TurnStarted, redact, record opened
└─ reveal to the farmer            edge: reveal kept values, record closed, turn trace
   └─ translate out                translation: the core's events → the farmer's language
      └─ the core                  intent ∥ moderation → discovery → planner → composer
         ↑ translate in            translation: the farmer's turn → the route's language
```

- **The edge** knows the farmer's redacted turn and the kept values. It
  writes the turn record and the turn trace, and reveals kept values in
  everything the farmer gets. It reveals after translation, so the translator
  only ever sees tags (driver 1).
- **Translation** knows both languages. With no route for the turn's
  language, it passes the turn and every event straight through (driver 2).
- **The core** runs on the turn it is given, as if it were the whole turn
  (driver 4). It yields tags only.

**Which languages, and into what.** `configs/edge-translation.yaml` lists
them. Translation is off unless `DSS_EDGE_TRANSLATION_CONFIG_PATH` points at
it. Each entry is a route: a translator, and the language the pipeline runs
in for it (`to`).

- `to` is set per language and need not be English. A Bhili turn routed to
  Marathi becomes a normal Marathi turn, which then uses the Marathi prompts
  like any Marathi turn.
- A route is found by the turn's `target_lang` language, so `gu-IN` uses the
  `gu` entry.
- A `to` that is the entry's own language, or another listed language, stops
  the boot: no loops, no chains.

**Direct or translate, per language** (beside ADR-0018). A deployment serves
each language one of two ways:

- **Direct:** the models read and write the language, with per-language
  prompts and the localizer (ADR-0018).
- **Translate:** this ADR. The pipeline runs in `to`, which is then served
  directly.

A language should not be both. A `gu` prompt is never used while `gu` is
translated, because the pipeline sees `to`.

**Translate in.** Right after redaction, one call translates the question,
the enriched question (when it differs) and the history together. The turn
then carries on with both of its languages set to `to`.

**Translate out, always streamed** (driver 6). The layer waits for the core's
final event, which holds the whole answer, and streams each block's
translation to the farmer, followed by that block's claim. This holds for
every reply: the composed answer and the fixed replies (refusal,
clarification, no provider) alike. Sources were worked out by the core from
the untranslated text, and stay as they are.

**Two adapters.**

- `llm`: a model translates. A turn goes in one structured call (a list in,
  a list out); an answer comes back as the model's own stream. The entry names
  its `model`. Its instructions are the `translator` prompt in the prompt
  registry, English only on purpose: they instruct the model, whatever
  language the text is in. The prompt orders tags, markers, numbers, units
  and scheme names kept as written (driver 7).
- `stub`: no model, for local runs. Any turn in its language becomes "Will
  it rain today?", and answers come back marked `[gu]`. `DSS_STUB_LLM=true`
  puts every language on it.

**Failure.** A translation failure fails the turn. There is no fallback and
no new cause code. A reply of the wrong length is a failure too.

**Observability.** Translation in and translation out are two stages,
`translation_in` and `translation_out`, each with its own span, duration and
model binding. The turn trace keeps the farmer's question and answer; the
`to`-language text appears only on the translation spans.

## 5. Consequences

- Good: a deployment serves a weak language through a strong one with one
  config entry. Languages not listed are untouched.
- Good: the translator only ever sees tags. Kept values are revealed after
  translation, at the edge.
- Good: the pipeline has no translation code. It is the same pipeline for a
  translated turn and a direct one.
- Good: the three layers have the shape `TurnRunner` wrappers would have. Making
  them full runners is the next step, once the reveal step and the turn trace
  can move out of `run()`.
- Trade-off: **a translated answer starts later.** The whole answer must be
  written before translation can start (the constraint recorded with
  ADR-0011). The farmer waits for the full composition, then the translation
  streams. Untranslated turns keep streaming as before.
- Trade-off: a translated fixed reply now arrives as pieces, then its claim.
  Untranslated fixed replies are still one final frame. A reply with several
  blocks costs one translation call per block, one after another.
- Trade-off: a fixed reply into a `to` other than English costs two model
  calls: the localizer into `to`, then the translation.
- Trade-off: `turns.jsonl` holds the farmer's language only. The text the
  pipeline actually read is in the traces, which keep it only as long as the
  tracing store does.
- Risk accepted: the DSS has no language detection. It assumes the farmer
  writes in `target_lang`. English typed with `target_lang=gu` is sent to the
  translator as Gujarati; an LLM translator usually copes.

**Open questions**

- **Route by `source_lang`.** Readers of the farmer's text (intent,
  moderation, translate in) would key on the language the farmer typed;
  writers (composer, localizer, translate out) on `target_lang`. This needs a
  reliable `source_lang`, for example from a language detector in the
  channel.
- **Fall back instead of failing.** On a translation failure, answer in the
  farmer's language directly rather than fail the turn.
- **A cause code** for translation failure, if callers need to tell it apart.
- **Cache translated history** per question and answer, instead of
  translating it again every turn.
- **The `to`-language text in the audit record,** if `turns.jsonl` becomes
  the official audit store.
