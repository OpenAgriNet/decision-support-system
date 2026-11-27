# oan-evals — Langfuse as the dataset store and results dashboard

**Status:** draft for discussion
**Date:** 2026-11-27
**Companion:** `oan-evals-repo-structure.md` (the repo this plugs into). This document fills in the two boxes the diagram leaves outside the repo — **Langfuse** and the **Langfuse plug** — and says exactly what goes over the wire.
**Rule we keep:** *files are the truth, Langfuse holds a copy.* Nothing below makes Langfuse load‑bearing for producing a result; it makes it the place people look at results.

---

## 1. What we already have

| Fact | Where verified | Why it matters |
|---|---|---|
| The DSS self‑hosts **Langfuse v4** (web + worker + Postgres + ClickHouse + Redis + MinIO) and reaches it over **OTLP, not the Langfuse SDK** | `decision-support-system/docker-compose.langfuse.yml`, `docs/ADR/0007-langfuse-for-llm-tracing.md` | v4 means the Scores API **v3** and first‑class Experiments; the deprecated v2 score endpoints return 404 on a fully migrated v4 |
| Every DSS turn is a trace; `sessionId` is `langfuse.session.id` on the root span, `transactionId`/`messageId` are trace metadata | `src/dss/adapters/observability/tracing.py:115` | An eval record can be joined to the DSS's own trace by `transactionId`, which the adapter chooses |
| The legacy bot also traces to a Langfuse; the current eval reads its TOOL observations by `sessionId` after each call | `oan-evaluation/inference/run_sequential_scenario.py:110–175` | Same join is possible for legacy, by `sessionId` |
| `oan-evaluation/src/tasks/` is **HuggingFace + Logfire**, not Langfuse datasets | `src/tasks/README.md` | There is no existing Langfuse dataset code to lift; the sink is new but small |
| Langfuse features we rely on: Datasets (versioned), Experiments with baseline + compare view and per‑score charts, **run‑level scores**, score configs, Annotation queues, Scores Analytics, a GitHub Action that fails CI on a run‑level score | Langfuse docs/changelog (sources at the end) | Each maps onto a box in the diagram — see §2 |

---

## 2. Mapping the diagram onto Langfuse objects

| Diagram box | Langfuse object | Direction | Notes |
|---|---|---|---|
| **Pool** | — (stays `pool/pool.jsonl`) | — | PII‑cleaned questions; the truth. Langfuse never holds a question we have not already vetted into the pool. |
| **Suite** | **Dataset** (one per suite: `smoke`, `regression`, `release`) | push | Item id = pool question id, so the same question in two suites is the same item id. Pin the **dataset version** when a run starts; record it in `manifest.json`. |
| **One data set (answer key)** | `expected_output` on each dataset item | push | Empty until `answer_key.jsonl` exists; Langfuse tolerates null. |
| **A run** (variant × suite) | **Experiment** (a.k.a. dataset run) | push | Name = `run_id`. Metadata = `variant {system, version, config}`, judge profiles, prompt shas, adapter sha, mockdata sha. |
| **Record** (one per turn) | **Trace** in the *eval* project, linked to the dataset item under the experiment | push | See §3 for why the eval project owns its own trace instead of borrowing the system's. |
| **Score** (one per metric per turn) | **Score** on that trace: numeric `value` (normalised 0–1), `comment` = reason, `metadata` = raw + judge pin | push | Registered as **score configs** so the UI aggregates them and annotators get the same scale. |
| **Verdict** on a run / report card | **Run‑level score** on the experiment: `report_card_verdict` (categorical: pass / fail / unsure) + `report_card_delta_<metric>` (numeric) | push | **This closes the diagram's open item** — Langfuse has held scores at dataset‑run level since May 2025 (`datasetRunId` on the Scores API). |
| **Report card** (two runs) | **Experiments compare view** with the baseline run selected | view | Langfuse shows per‑item and per‑score deltas, cost and latency, colour‑coded against the baseline. The *numbers* are still computed in `framework/report/report_card.py`; Langfuse displays them. |
| **"Ask a person"** | **Annotation queue** `eval-unsure` + human score configs | push, then pull | Sink enqueues every trace with any `label == "unsure"`. A reviewer scores it in the UI. `oan-evals decide --pull` writes those annotations into `runs/<id>/decisions.jsonl` — files stay the truth. |
| **Judge model + prompt pinned** | Score metadata (`judge.model`, `judge.prompt_version`, `judge.prompt_sha256`) | push | Optionally mirror prompt files into Langfuse **Prompt Management** for browsing; the repo file stays canonical. |
| **Rules** (thresholds, margins) | — (stays `rules/thresholds.yaml`) | — | Langfuse's compare view has a score‑threshold filter, but the pass line is ours. |
| **Mock doors / mockdata** | — | — | Not a Langfuse concern. |

---

## 3. One design decision: whose trace is the Record?

Two options.

**A. Link the system's own trace.** The DSS already emits a trace per turn; the adapter picks the `transactionId`, so the sink could call `dataset_item.link(trace_id=<transactionId>, run_name=run_id)`. Legacy likewise by looking up the trace for the `sessionId`.
*Problem:* datasets, experiments and scores live inside **one Langfuse project**, and the DSS and the legacy bot trace into different projects (very likely different instances). A cross‑project link is not possible, and the framework would have to know which system traces where — exactly the knowledge the diagram keeps out of the framework.

**B. The eval owns its trace (recommended).** `framework/runner/ask.py` opens one span per turn (OpenTelemetry, same transport ADR‑0007 chose) in a dedicated **`oan-evals` Langfuse project**. The span's input is the question, its output the answer, its metadata the whole `Record` minus `raw`, plus `system_trace_url` pointing at the system's own trace wherever it lives. Scores attach to this span. The system's trace remains the place to debug *why* an answer was bad; the eval trace is the place to see *that* it was.

B keeps the framework system‑neutral, works identically for X and Y, and means a system that does not trace at all (a fine‑tuned checkpoint behind vLLM, say) is still fully represented. The cost is one extra trace per turn, which is small.

---

## 4. The sink — what `adapters/langfuse/sink.py` actually does

```python
class LangfuseSink(Sink):
    """Push a run's files to Langfuse. Idempotent: re-pushing the same run_id updates, never duplicates."""

    def put_dataset(self, suite: Suite, pool: Pool) -> str:            # suite → dataset, returns dataset version
    def put_run(self, manifest: Manifest) -> None:                      # experiment with variant metadata
    def put_records(self, records: Iterable[Record]) -> None:           # one trace per record, linked to its item under the run
    def put_scores(self, scores: Iterable[Score]) -> None:              # per-trace scores; "unsure" → annotation queue
    def put_report(self, card: ReportCard) -> None:                     # run-level scores on the candidate run; baseline id in metadata
    def pull_decisions(self, run_id: str) -> list[Decision]:            # annotation-queue results → decisions.jsonl
```

Wire‑level details worth fixing now:

- **SDK:** `langfuse` Python SDK ≥ 4.6 (needed for run‑level scores and the experiment API on v4). The DSS's own rule of "no Langfuse SDK in the app" applies to the DSS, not to the eval repo; the sink is exactly the kind of adapter that is allowed to import it.
- **Score configs** are created once per metric from `rules/metrics.yaml` (`name`, `dataType=NUMERIC`, `minValue=0`, `maxValue=1`) plus `human_<metric>` twins for the annotation queue, and `report_card_verdict` as `CATEGORICAL [pass, fail, unsure]`.
- **Idempotency:** trace id = deterministic hash of `(run_id, question_id, turn_index)`; score id = hash of that plus `metric` and `judge.prompt_sha256`. Re‑running `push` after a re‑mark updates scores in place; a changed prompt sha produces a *new* score next to the old one, which is what "a prompt change invalidates historical comparison" needs.
- **What is never pushed:** `Record.raw` (adapter‑specific response blobs), mockdata, anything under `pool/` that is not already in a suite.

CLI:

```bash
oan-evals push-dataset --suite smoke                     # pool → Langfuse dataset, prints version
oan-evals push runs/2026-11-27_dss                       # experiment + traces + scores (+ enqueue unsure)
oan-evals push runs/2026-11-27_dss --report              # also run-level verdict vs the baseline named in report.json
oan-evals decide runs/2026-11-27_dss --pull              # annotation queue → decisions.jsonl
```

---

## 5. The dashboard — what people open

Langfuse gives three views for free; one needs setting up.

| Question someone asks | Where they look | Set‑up |
|---|---|---|
| "Is the DSS better than legacy on this suite?" | **Experiments → select both runs → Compare**, baseline = legacy run. Per‑score deltas, cost, latency, item‑by‑item. | None beyond pushing both runs |
| "Which metric moved, and on which questions?" | Same view: per‑score chart across runs; filter items by score threshold (Oct 2026 feature) | None |
| "How has `no_fabrication` trended across DSS versions?" | **Experiments** page, chart per score across all runs on the dataset; or **Scores → Analytics** for mean / distribution / correlation of two scores | Consistent experiment naming (`<date>_<system>@<version>+<config>_<suite>`) |
| "Where are the items a person must decide?" | **Annotation queue `eval-unsure`** | Queue + human score configs created by the sink |
| "Pass rate by category / language, p95 latency by variant, judge cost per run" | **Custom dashboard** with widgets filtered on trace metadata (`category`, `language`, `variant.system`) | Build once per project; Langfuse documents custom dashboards for v3+, **verify the widget set on our v4 instance** before promising specific charts |
| "Did this PR regress the release suite?" (later) | PR comment from `langfuse/experiment-action`, which fails the job when a run‑level score is below the bar | GitHub Action wiring; only once the release suite exists |

What Langfuse will **not** do for us, so the framework still must:

- Decide "compared only on metrics both runs can fill". The compare view shows blanks; the report card excludes them.
- Normalise rubric scales. Scores must already be 0–1 when pushed.
- Apply margins (`min_margin`, `min_n`). The verdict is computed in `report_card.py` and pushed as a run‑level score; Langfuse's threshold filter is a browsing aid, not the rule.
- Hold the mockdata or the doors. A run is only reproducible if `manifest.json` records the mockdata sha alongside the dataset version.

---

## 6. Data shapes on the wire

**Dataset item** (one per pool question in the suite):

```json
{
  "id": "q-mandi-0012",
  "input":  {"question": "आज सोलापूर मंडीत कांद्याचा भाव काय?", "language": "mr", "category": "mandi_price",
             "prior_turns": []},
  "expected_output": {"answer": null, "expected_status": "answered", "must_contain": []},
  "metadata": {"pool_id": "q-mandi-0012", "tags": ["smoke", "mandi"], "source": "benchmark_questions.csv"}
}
```

**Trace** (one per Record), in the `oan-evals` project:

```json
{
  "id": "<hash(run_id, question_id, turn_index)>",
  "name": "eval-turn",
  "session_id": "<session_id>",
  "input":  {"question": "...", "prior_turns": []},
  "output": {"answer": "...", "status": "answered", "sources": ["..."]},
  "metadata": {"run_id": "...", "variant": {"system": "dss", "version": "0.4", "config": "base"},
               "category": "mandi_price", "language": "mr",
               "calls": [{"name": "openagrinet:MandiPrice", "latency_ms": 212}],
               "ttft_ms": 480, "total_ms": 2310, "system_trace_url": "https://langfuse.dss/.../traces/<transactionId>"}
}
```

**Score** (one per metric per trace):

```json
{"traceId": "...", "name": "no_fabrication", "value": 1.0, "dataType": "NUMERIC", "comment": "<judge reason>",
 "metadata": {"raw": 1, "label": "pass", "judge": {"profile": "vllm-gemma", "model": "gemma-4-31b-it",
              "prompt_version": "v1", "prompt_sha256": "…", "temperature": 0.0}, "cost_usd": 0.0}}
```

**Run‑level score** (the verdict, on the candidate run):

```json
{"datasetRunId": "<dss run>", "name": "report_card_verdict", "dataType": "CATEGORICAL", "value": "pass",
 "comment": "6/6 shared metrics within margin; no_fabrication +0.04 (n=120)",
 "metadata": {"baseline_run_id": "<legacy run>", "shared_metrics": ["..."], "min_margin": 0.05}}
```

---

## 7. Open points to settle

1. **One `oan-evals` Langfuse project — on which instance?** The DSS team runs a v4 instance already; the simplest answer is a second project on it. Needs keys and a decision on who administers it.
2. **Custom dashboards on v4.** Documented for v3+; confirm on our instance which widget types and filters exist before §5's last row is promised to anyone.
3. **Dataset versions vs. pool edits.** When a question is reworded in `pool.jsonl`, `push-dataset` creates a new dataset version; old experiments stay pinned to the old one. Agree that a reworded question is a *new* pool id (so old and new never share an item id) — otherwise compare views mix the two.
4. **PII.** The pool is PII‑clean by rule, so nothing pushed from it is a problem. `system_trace_url` is a link, not content. Reviewers in the annotation queue see only the eval trace.
5. **Judge calibration data** (human labels per judge) can live in the same annotation queue mechanism — `human_<metric>` scores next to the judge's — which gives Cohen's κ for free from the Scores Analytics correlation view. Worth doing from day one rather than as a later phase.

---

## Sources

- [Evaluate with Datasets — Langfuse](https://langfuse.com/docs/evaluation/get-started/offline)
- [Experiments as a First‑Class Concept (Apr 2026) — Langfuse](https://langfuse.com/changelog/2026-04-13-experiments-rebuild)
- [Experiments show which metric moved, and why (Oct 2026) — Langfuse](https://langfuse.com/changelog/2026-10-19-experiments)
- [Compare experiments — Langfuse](https://langfuse.com/docs/evaluation/experiments/compare-experiments)
- [Dataset Run Level Scores (May 2025) — Langfuse](https://langfuse.com/changelog/2025-05-07-run-level-scores)
- [Scores API v3 (Jun 2026) — Langfuse](https://langfuse.com/changelog/2026-06-10-scores-v3-api)
- [Annotation Support in Experiment Compare View — Langfuse](https://langfuse.com/changelog/2025-10-23-annotate-from-compare-view)
- [How to retrieve experiment scores via UI or API/SDK — Langfuse](https://langfuse.com/faq/all/retrieve-experiment-scores)
- [Golden dataset evaluation — Langfuse](https://langfuse.com/resources/engineering/golden-dataset-evaluation)
- [LLM Evaluation: Methods and Best Practices — Langfuse](https://langfuse.com/blog/2025-11-12-evals)
- `decision-support-system/docs/ADR/0007-langfuse-for-llm-tracing.md`, `src/dss/adapters/observability/tracing.py`
