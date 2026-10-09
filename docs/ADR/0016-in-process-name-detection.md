# ADR-0016: in-process name identification with spaCy

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

**One more PII identifier on the ADR-0015 port: spaCy `en_core_web_sm`**, which
an adopter turns on by listing it in the rules file's `identifiers:` beside the
regex one. The example file leaves it off, because it reads crop words as names:

```yaml
identifiers:
  - type: regex
    rules: [...]
  - type: spacy
    entity: person
```

- `adapters/pii_identifier/spacy/`. `en_core_web_sm` is a main dependency,
  loaded with only its entity recogniser.
- It only reports spans. Whether a name is kept or destroyed is the
  `entities:` policy's call, in core.
- It runs side by side with the regex identifier, on a worker thread. If it
  fails, the regex spans still apply and the result names it in
  `Redaction.failed`.
- A missing model stops the boot. It is never discovered on a turn.

**IndicNER is the next step, not part of this decision.** It was the only small
model that found names in Indian scripts (§3). It is built and tested on the
branch `feat/136-onnx-identifier`: an `onnx` identifier that loads any
token-classification model exported to ONNX, a script that compresses IndicNER
to int8 (667 MB → 168 MB), and a `dss-indicner` image target that bakes it in.
It is held back for now: about 390 MB of memory, a gated download that needs a
Hugging Face token to build, and an extra image target to maintain.

## 5. Consequences

**Good**

- A name in English is found without being announced.
- No token, no extra build step, no new image target.
- Adding IndicNER later is one more identifier and a config entry; core and
  orchestration do not change.

**Known gaps — accepted for now**

- **spaCy is wrong on romanised Hinglish.** On *"mera naam Suresh Patil hai,
  gehu ka rate kya hai?"* it misses the name and tags *"mera naam"* and
  *"gehu"* (wheat) as people. It gives no score, so nothing filters these out.
  An adopter with Hinglish traffic should leave the `spacy` entry out.
- **English only.** Names in Indian scripts are not found until IndicNER lands.
- **It misses many Indian names, even in English sentences.** It finds "Sunita
  Devi" and "Priya Sharma", but not "Anil Kumar" in *"Call Anil Kumar on ..."*,
  "Suresh Yadav" in *"Suresh Yadav's cow has a fever"*, or "Ramesh Patil" unless
  he announces it (*"my name is ..."*, which the regex identifier catches).
- **Crop and variety words that are also names** (Kamal, Tulsi) may be
  replaced. Not handled here.
- **Cost grows with history.** spaCy runs once per text, about 2 ms each.
- **No accuracy measured on our own data yet.** The figures above are published
  numbers and a smoke test.
