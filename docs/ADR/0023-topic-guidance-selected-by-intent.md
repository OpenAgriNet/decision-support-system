# ADR-0023: Topic Guidance Selected by Intent

- **Status:** PROPOSED
- **Date:** 2026-11-11
- **Deciders:** DSS implementation
- **Extends:** [ADR-0018](0018-per-language-prompts-through-configuration.md)

---

## 1. Context and Problem Statement

Some answers need rules that only make sense for one kind of question. A
mandi price answer must name the market and the date of each price, and say
when the farmer's own market had none (#242). Put into the composer's prompt,
that rule is read on every turn — a weather or scheme answer carries mandi
rules it can only misapply, and each new topic makes the general prompt
longer and harder to review.

The intent component already decides what each ask is about: every ask has
one subject category (Crop, Weather, Market, ...). That is the place to decide
which topic rules a turn needs.

## 2. Decision Drivers

1. The general prompt stays general. A topic's rules are read only on a turn
   about that topic.
2. Intent decides what a turn is about, once; later stages do not guess again.
3. A topic's rules are prompt text, so a reviewer edits them without a code
   change, per language, as ADR-0018 set up.
4. A broken config refuses to boot (ADR-0018).

## 3. Considered Options

1. **Topic guidance in the prompt registry.** `configs/prompts.yaml` maps an
   intent category to a guidance file, per component and language. The
   component's template has one `topic_guidance` placeholder, filled with the
   guidance for the categories the turn's asks carry.
2. **`{% if %}` branches in the template.** Every topic's text stays inside
   the general prompt file, so the file grows with each topic and the
   reviewer still reads them all.
3. **Rules in code**, appended by the composer for a known category. A
   reviewer cannot change them, and they skip the per-language mechanism.

## 4. Decision Outcome

Chosen option: **1**.

- **Config.** A language entry may carry `topics: {<category>: <file>}`. The
  key must be an intent category, and the file must exist, or the DSS refuses
  to boot. A language with no `topics` uses English's, as skills do.
- **Port.** `PromptProvider.get_topic_guidance(identifier, lang, categories)`
  returns the guidance for those categories, in configured order, joined by a
  blank line — or nothing.
- **Composer.** The composer's prompt declares `topic_guidance`, filled from
  the subject categories of the turn's asks. Market is the first topic: the
  rule to name each price's market and date, and to say when the asked market
  had none.

## 5. Consequences

- **Good:** a turn about weather no longer reads mandi rules. Adding a topic
  is a file and a config line, per language.
- **Good:** the same mechanism serves any component that declares the
  placeholder; only the composer does today.
- **Bad:** a topic's guidance is chosen by category, not by what was found. A
  Market ask that ends with no data still gets the market rules — harmless,
  since they only speak about prices that are there.
- **Neutral:** code for one kind of data stays gated on the data's type, not
  on intent's category. See §6.

## 6. Prompt Text by Category, Code by Data Type

Two kinds of topic-specific behaviour exist, and each is gated by a
different check:

| What | Gated on | Where |
|---|---|---|
| Prompt text (the Market guidance) | the ask's intent category | `core/channel/prompt.py` |
| The `/select` validity window | the capability being called (`openagrinet:MandiPrice`) | `core/planner/resource_attributes.py` |
| Picking the asked market's prices, or the other markets' | the capability being called | `core/planner/mandi_prices.py` |

The code already runs only for a Market ask, through a chain intent starts:

1. Intent tags the ask `Market`.
2. `/discover` filters on that category, so only Market resources come back.
3. The planner can `/select` only what discovery returned.
4. So a MandiPrice `/select` comes only from a Market ask.

Adding an intent check to the code was considered and not done:

- **It repeats a check.** `category == Market and capability == MandiPrice`
  selects nothing that `capability == MandiPrice` does not.
- **The data type is the true reason.** The validity window exists because
  Agmarknet's MandiPrice endpoint refuses a select without one — a fact about
  the endpoint, not the question. A future Market resource of another type
  (a price-trend provider with no such rule) would wrongly get the window
  under a category check, and correctly not get it under a type check.
- **The code reads that type's fields.** Price picking reads
  `market.marketName` and `arrivalDate`, which only MandiPrice guarantees.

The rule: what the farmer asked decides which text a prompt carries; what
data is being handled decides which code runs.
