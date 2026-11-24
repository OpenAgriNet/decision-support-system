# ADR-0025: Category Guidance Selected by Intent

- **Status:** PROPOSED
- **Date:** 2026-11-11
- **Deciders:** DSS implementation
- **Extends:** [ADR-0018](0018-per-language-prompts-through-configuration.md)

---

## 1. Context and Problem Statement

Some answers need rules that only make sense for one kind of question. A
mandi price reply covers a whole district over several days, so the answer must
pick the asked market's price, or say it had none and give the others', each
with its market and date (#242). Put into a component's general prompt, or
into the planner's code, that rule runs on every turn — a weather or scheme answer carries mandi
rules it can only misapply, and each new category makes the general prompt
longer and harder to review.

The intent component already decides what each ask is about: every ask has
one subject category (Crop, Weather, Market, ...). That is the place to decide
which category's rules a turn needs.

## 2. Decision Drivers

1. The general prompt stays general. A category's rules are read only on a
   turn about that category.
2. Intent decides what a turn is about, once; later stages do not guess again.
3. A category's rules are prompt text, so a reviewer edits them without a code
   change, per language, as ADR-0018 set up.
4. A broken config refuses to boot (ADR-0018).

## 3. Considered Options

1. **Category guidance in the prompt registry.** `configs/prompts.yaml` maps an
   intent category to a guidance file, per component and language. The
   guidance for the categories the turn's asks carry goes into the
   component's prompt in one place.
2. **`{% if %}` branches in the template.** Every category's text stays inside
   the general prompt file, so the file grows with each category and the
   reviewer still reads them all.
3. **Rules in code**, in the planner or the composer, for a known category or
   data type. The generic flow gains a branch per category, a reviewer cannot
   change the rules, and they skip the per-language mechanism. An earlier
   version of #242 picked the asked market's prices in the planner's code;
   review asked for the planner to stay free of any one capability.
4. **An Agent Skill per category, chosen by intent.** The planner is offered
   the category's skill through the Agent Skills capability of
   `pydantic-ai-harness` (`Skills`), and loads it when it needs it. The
   composer is not an agent with tools, so it still needs option 1.
5. **A slot in the planner's existing skill**, filled like the composer's
   placeholder. One mechanism for both components, but every skill file
   becomes a template, and the registry needs code to fill and check it.

## 4. Decision Outcome

Chosen options: **1** for the composer, **4** for the planner. Option 5 was
built first; review asked for the planner's rules to load as a skill rather
than be joined into one by our code.

- **Config.** A language entry may carry `category_guidance: {<category>: <file>}`. The
  key must be an intent category, and the file must exist, or the DSS refuses
  to boot. A language with no `category_guidance` uses English's, as skills do.
- **Port.** `PromptProvider.get_category_guidance(identifier, lang, categories)`
  returns the guidance for those categories, in configured order, joined by a
  blank line — or nothing.
- **Composer.** It is given the guidance for the subject categories of the
  turn's asks, in a `{{ category_guidance }}` placeholder in its template.
  Guidance with no placeholder to go into refuses to boot. It is told to give
  the asked market's latest price, or say it had none and give each other
  market's latest, and to name the market and date of every price.
- **Planner.** A language entry may carry `capability_skills: <folder>`, a
  folder of Agent Skills, one per intent category, each
  `<category in lowercase>/SKILL.md` (`market/SKILL.md`). A folder named for
  no intent category, or a missing folder, refuses to boot.
  `PromptProvider.get_capability_skills(identifier, lang, categories)` names
  the skills for the turn's categories, and the planner agent is built with
  `Skills(..., include=those names)`. The model sees each offered skill's
  name and description, and loads its rules with `load_capability`; its
  skill tells it to load a listed one before `select`. The library is read
  through a read-only workspace of its own, so the planner gets no file
  tools. The Market skill tells the planner which days to ask prices for,
  from the question's dates and today's date, and that other markets'
  prices in the district still answer the ask, so it keeps them.

## 5. Consequences

- **Good:** a turn about weather no longer reads mandi rules. Adding guidance
  for a category is a file and a config line, per language.
- **Good:** the same mechanism serves any component that asks for its
  guidance; the planner and the composer do today.
- **Good:** the planner's and the composer's code stay generic. A category's
  rules change prompt text, never the flow.
- **Bad:** following a category's rules is up to the model, not code. Picking
  the asked market's latest price is no longer guaranteed, and the composer
  reads every market's rows for those days, which costs more tokens.
- **Bad:** guidance is chosen by category, not by what was found. A
  Market ask that ends with no data still gets the market rules — harmless,
  since they only speak about prices that are there.
- **Bad:** the planner reads its category's rules only if it loads the
  skill. A turn where it does not gets no Market rules, and one where it does
  costs one more model round trip.
- **Bad:** a new dependency. `pydantic-ai-harness` pins one exact
  `pydantic-ai-slim` version per release (0.54.0 pins 2.54.0), so the two
  are upgraded together. It is pre-1.0, and its API may change between minor
  releases.
- **Neutral:** two kinds of skill now sit beside each other: the planner's
  own (`skills:`, always in its prompt, binding its tools) and Agent Skills
  (`capability_skills:`, loaded on demand, instructions only).
- **Neutral:** the one category-specific line of code left is the MandiPrice
  validity window. See §6.

## 6. What Stays in Code

A category's rules are prompt text, chosen by intent's category. The planner's
and the composer's code carry no branch for a category or a data type.

One exception stays, and it is not a rule about answers. Agmarknet refuses
a MandiPrice `/select` without a validity window. The pack lists `validity`
as a field a caller may send, so the planner sets it, and the Market
guidance says which days: a named period as asked, the week up to a named
day, and the week up to today when no day is named or it is after today. The
planner's prompt carries today's date, for any capability, so it can work
the days out.

The model may still leave the window out, so
`core/planner/resource_attributes.py` adds the week up to today when the
capability called is `openagrinet:MandiPrice` and no window was sent. It is
keyed on the data type, not the category, because it is a fact about that
endpoint: another Market resource without the rule must not get it.
