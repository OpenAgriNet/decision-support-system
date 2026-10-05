# ADR-0016: in-process name detection — spaCy by default, IndicNER optional

- **Status:** PROPOSED
- **Date:** 2026-10-05
- **Deciders:** DSS code owners
- **Informed:** Adopter engineering teams; OAN DPG steward
- **Issue:** OpenAgriNet/engineering-tracker#136

---

## 1. Context and Problem Statement

ADR-0015 removes identifiers with a fixed shape, and a name only when the farmer
announces it ("my name is ..."). A name said any other way — *"Ramesh ke khet
mein"* — reaches the model and the trace store.

Finding names needs a model. The DSS runs in a 1 CPU / 1 GiB container
(ADR-0013), so the model has to be small, run on a CPU, and not bring torch.

**The question.** Which model, where does it run, and how does an adopter pick
another one?

## 2. Decision Drivers

- **Small.** Disk and memory both count in a 1 GiB container.
- **No torch at runtime.** It is hundreds of MB on its own.
- **Indian languages.** Farmers write in Hindi, Marathi and others.
- **Swappable by config.** A country forking the platform brings its own model.
- **Never costs the turn.** A broken model falls back to patterns.

## 3. Considered Options

Two research passes compared about 30 models, each measured on the same short
farmer questions (dev Mac, one thread):

| Model | Disk | Memory | Finds names in | Verdict |
|---|---|---|---|---|
| spaCy `en_core_web_sm` | ~15 MB (+ spaCy ≈ 85 MB installed) | +60 MB | English | Small and fast (~2 ms); wrong on Hinglish |
| spaCy `xx_ent_wiki_sm` | 11 MB | +463 MB | European languages, capitalised | No Indian language |
| IndicBERT v1 fine-tunes, int8 | 62 MB | +438 MB | — | Drops Hindi vowel signs; no licence |
| nym PII small, int8 | 108 MB | ~630 MB | English, Hindi | Too much memory |
| **IndicNER (mBERT), int8 ONNX** | **168 MB** | **~390 MB** | **English + 11 Indian languages** | **Only one that works** (Hindi name F1 0.82, published) |
| GLiNER x-small, int8 | 173 MB | ~1.27 GB | Many | Needs torch |
| Stanza Hindi | 116 MB+ | — | Hindi | Needs torch |

**Where it runs.** A separate model service over HTTP keeps the image small and
scales on its own. Rejected for now: it is one more thing to run. The
`PiiIdentifier` port (ADR-0015) means an HTTP identifier can be added later as
another adapter.

## 4. Decision Outcome

**Two more PII identifiers on the ADR-0015 port**, listed in the rules file's
`identifiers:` beside the regex one:

```yaml
identifiers:
  - type: regex
    rules: [...]
  - type: spacy                        # the default name identifier
    entity: person
  # - type: onnx                       # optional: IndicNER or any ONNX name model
  #   dir: /app/var/models/indicner
  #   min_score: 0.8
```

- **`spacy`** — `en_core_web_sm`, a main dependency
  (`adapters/pii_identifier/spacy/`). Loaded with only its entity recogniser.
- **`onnx`** — any token-classification model in a folder of `model.onnx`,
  `tokenizer.json` and `labels.json` (`adapters/pii_identifier/onnx/`).
  IndicNER is the documented one. Needs the `ner-indic` extra: `onnxruntime`
  and `tokenizers`, no torch. Another model is another folder, not a code
  change.

Both only report spans. Whether a name is kept or destroyed is the `entities:`
policy's call, in core. They run side by side with the regex identifier, on
worker threads; if one fails, the others still apply and the result names it
in `Redaction.failed`.

**We build our own compressed copy of IndicNER.** No official ONNX or int8
version exists. `scripts/export_onnx_ner.py` downloads the model, exports it,
and compresses it to int8 (667 MB → 168 MB). The `dss-indicner` image target
runs it in a builder stage, so torch never reaches the running image and the
model is in the container from the start — no download at runtime. IndicNER
is MIT-licensed, which allows this; credit goes to AI4Bharat.

**A missing model stops the boot**, naming the missing file or library. It is
never discovered on a turn.

## 5. Consequences

**Good**

- A name in English or an Indian script is found without being announced.
- The default image needs no token and no extra build step.
- An adopter swaps the model by building a different folder.

**Known gaps — accepted for now**

- **spaCy is wrong on romanised Hinglish.** On *"mera naam Suresh Patil hai,
  gehu ka rate kya hai?"* it misses the name and tags *"mera naam"* and
  *"gehu"* (wheat) as people. It gives no score, so nothing filters these out.
  It is the default because it is small; an adopter with Hinglish traffic
  should use `onnx` or `none`.
- **IndicNER on romanised Hinglish depends on context.** *"mera naam Suresh
  Patil hai"* scores 0.9; the same with *"gehu ka rate kya hai?"* after it finds
  nothing; *"Ramesh ke khet mein"* scores 0.65, under the 0.8 default.
- **Crop and variety words that are also names** (Kamal, Tulsi) may be
  replaced. Not handled here.
- **IndicNER uses about 390 MB of memory**, the biggest single user in a
  1 GiB container.
- **The IndicNER download is gated** (automatic approval). Building the folder
  needs a Hugging Face token; running the DSS does not.
- **Cost grows with history.** The model runs once per text, ~11 ms each for
  IndicNER on a Mac (1 CPU in a container: an estimated 2–3× slower).
- **No accuracy measured on our own data yet.** The figures above are published
  numbers and a smoke test.
