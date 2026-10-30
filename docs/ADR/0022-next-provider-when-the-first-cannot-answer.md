# ADR-0022: Use the Next Provider When the First Cannot Answer

- **Status:** ACCEPTED
- **Date:** 2026-10-30
- **Deciders:** DSS implementation
- **Amends:** [ADR-0019](0019-client-preferred-providers.md) — its "returned anything" test

---

## 1. Context and Problem Statement

ADR-0019 answers an ask from the first ranked provider that returned
anything. One result counts, even one about something else.

On dev, a Maharashtra farmer asked how to grow Red Banana in a hill area.
The Maharashtra provider came first and returned one passage about custard
apple and livestock. Bharat Vistaar came second and returned three passages
on bananas in hill areas. The DSS used the first and told the farmer it
could not find an answer.

## 2. Decision Drivers

- A farmer should get an answer when a ranked provider has one.
- The ranking still decides the order. The model does not pick a vendor.
- One ask is answered by one provider. No merging.
- No extra latency or LLM call on the common path.

## 3. Considered Options

1. **Swap the ranking.** Registry change only. Loses the state provider's
   answers whenever it does have them.
2. **Provider cuts off weak matches.** Each provider returns nothing below a
   relevance score. Right in principle, but every provider must do it.
3. **A separate relevance check.** One extra LLM call per provider, in rank
   order. Predictable, but adds cost and about a second per provider.
4. **The planner judges, with a `select_next` tool.** The planner already
   reads the data. If it cannot answer the ask, the planner switches to the
   next ranked provider's data.

## 4. Decision Outcome

Option 4.

- `select` still consults every ranked provider at once, and still answers
  from the first one that returned anything.
- When a lower-ranked provider also returned data, `select` tells the planner
  it may call `select_next`.
- `select_next` replaces the ask's data with the next provider's. It calls
  nobody: the data was already fetched.
- The planner can call it again, down the list, until the data answers the
  ask or no provider is left.
- An unranked ask has no next provider. `select_next` says so.

Option 2 is still worth doing in the providers. It makes the first answer
better, and this rule works the same either way.

## 5. Consequences

- The Red Banana question is answered from Bharat Vistaar for a Maharashtra
  sender ranked `[Maharashtra, Bharat Vistaar]`.
- The model now judges whether data is enough. It can be wrong both ways:
  keep a thin answer, or skip a good one. Each switch is logged as
  `next_provider_used`, so the team can check.
- The first choice still wins whenever its data answers the ask.
- Answers the catalog already holds (Direct answers) are not covered. They
  never go through `select`, and still come from the best-ranked provider.
