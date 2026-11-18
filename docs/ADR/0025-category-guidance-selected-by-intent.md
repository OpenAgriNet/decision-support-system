# ADR-0025: Category Guidance Selected by Intent

- **Status:** PROPOSED
- **Date:** 2026-11-11
- **Deciders:** DSS implementation
- **Extends:** [ADR-0018](0018-per-language-prompts-through-configuration.md)

---

## 1. Context and Problem Statement

Some answers need rules that only make sense for one kind of question. A
mandi price reply covers a whole district over a week, so the answer must
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
4. **A whole skill per category, chosen by intent** ("skill discovery"). A
   skill binds tools and sets out how to call providers; the Market rules
   are a few lines that adjust that one procedure, not a new one. And the
   composer has no skills, so it would still need option 1. A slot in the
   existing skill gives the planner the same per-category selection with one
   mechanism for both components.

## 4. Decision Outcome

Chosen option: **1**.

- **Config.** A language entry may carry `category_guidance: {<category>: <file>}`. The
  key must be an intent category, and the file must exist, or the DSS refuses
  to boot. A language with no `category_guidance` uses English's, as skills do.
- **Port.** `PromptProvider.get_category_guidance(identifier, lang, categories)`
  returns the guidance for those categories, in configured order, joined by a
  blank line — or nothing.
- **Planner and composer.** Both are given the guidance for the subject
  categories of the turn's asks, in a `{{ category_guidance }}` slot. The
  planner's slot is in its skill (`provider-invocation.md`), so its prompt
  template is unchanged. The composer has no skills, so the slot is a
  placeholder in its template. A skill may use no other value, and guidance
  with no slot in the template or a skill refuses to boot. Market is the
  first category with guidance.
  The planner is told other markets' prices in the district still answer the
  ask, so it keeps them. The composer is told to give the asked market's
  latest price, or say it had none and give each other market's latest, and
  to name the market and date of every price.

## 5. Consequences

- **Good:** a turn about weather no longer reads mandi rules. Adding guidance
  for a category is a file and a config line, per language.
- **Good:** the same mechanism serves any component that asks for its
  guidance; the planner and the composer do today.
- **Good:** the planner's and the composer's code stay generic. A category's
  rules change prompt text, never the flow.
- **Bad:** following a category's rules is up to the model, not code. Picking
  the asked market's latest price is no longer guaranteed, and the composer
  reads every market's rows for the week, which costs more tokens.
- **Bad:** guidance is chosen by category, not by what was found. A
  Market ask that ends with no data still gets the market rules — harmless,
  since they only speak about prices that are there.
- **Neutral:** the one category-specific line of code left is the MandiPrice
  validity window. See §6.

## 6. What Stays in Code

A category's rules are prompt text, chosen by intent's category. The planner's
and the composer's code carry no branch for a category or a data type.

One exception stays, and it is not a rule about answers. Agmarknet refuses
a MandiPrice `/select` without a validity window, so
`core/planner/resource_attributes.py` adds one when the capability called is
`openagrinet:MandiPrice`. It was there before #242, which only widened it to
a week. It is keyed on the data type, not the category, because it is a fact
about that endpoint: another Market resource without the rule must not get
it. It goes when the schema pack fix (#55) lets the window come from the
pack.
