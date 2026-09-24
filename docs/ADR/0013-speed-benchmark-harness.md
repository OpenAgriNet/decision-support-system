# ADR-0013: a speed benchmark with real models and fake providers

- **Status:** ACCEPTED
- **Date:** 2026-09-23
- **Deciders:** DSS code owners

---

## 1. Context and Problem Statement

A change to a model or a prompt can make a turn slower. ADR-0012 records how
long each stage of one turn takes. But there is no fixed set of questions to
run and no tool that sums up the result. So "did my change make it slower?"
has no answer.

We want to measure **our** code and models, not the provider network.

## 2. Decision Drivers

1. Two runs must be comparable: same questions, same fake answers.
2. Time what a farmer waits for, including the network to the DSS.
3. No call may leave for a real provider.
4. It must not ship, and must not block a merge on time.
5. Keep it simple enough to run on a laptop.

## 3. Considered Options

- **DSS in-process or a real server?** Real server: it is timed as a client
  sees it.
- **Fake answers recorded from dev, or built?** Built from one file: no dev
  access, no drift.
- **Match on `@type` alone, or on key fields?** Key fields: one answer per
  type makes the composer write "I don't have that".
- **A test or a tool?** A tool: time has no pass or fail.
- **Load with a fake model or the real one?** Real, for now: a fake model must
  script the planner's tool calls.

## 4. Decision Outcome

- A CLI in `evals/perf/`, not a test. It does not ship: the image copies only
  `src/`.
- 30 fixed questions in `evals/perf/questions.toml`. The same file holds the
  mock's answers, so the two cannot drift apart.
- The mock answers only those questions. Prices and forecasts are seeded from
  the request, so each question gets the same numbers every run.
- A question it can't answer is a miss, counted and left out. The mock sends
  `400`, not `404`, because the DSS retries a `404` as transient.
- The client times the turn. Langfuse gives stage times and tokens, read
  through its REST API, not its SDK (ADR-0007).
- Load mode: N turns at a time, each worker waiting for its answer, so the
  cost is known up front. The DSS runs in a container limited to 1 CPU and
  1 GiB, so the figures mean one pod.
- Each result records the models, the commit and the machine.

## 5. Consequences

**Good**

- One command answers "did my change make it slower?".
- Runs compare: same questions, same answers, same limits.

**Bad**

- Each turn costs a real model call.
- The mock answers only the benchmark's questions.
- Provider speed is not measured, by design.
- Model speed varies through the day; only close runs compare.
- Waiting for each answer hides some queueing under load. A fixed arrival rate
  would show it, but with a real model its cost has no limit.

**Revisit when** a fake model is worth building (to find one pod's CPU limit),
or when the benchmark needs languages other than English.
