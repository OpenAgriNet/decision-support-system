# ADR-0025: Capability Tools Selected by Intent

- **Status:** PROPOSED
- **Date:** 2026-11-11
- **Deciders:** DSS implementation
- **Extends:** [ADR-0018](0018-per-language-prompts-through-configuration.md)

---

## 1. Context and Problem Statement

Some provider calls need rules that only make sense for one kind of question.
A mandi price `select` must say which days to get prices for, and its reply
covers the asked market's whole district over those days, so other markets'
prices still answer the ask (#242). Put into the planner's general prompt, or
into its code, those rules run on every turn: a weather or scheme turn carries
mandi rules it can only misapply, and each new category makes the general
prompt longer and harder to review.

The intent component already decides what each ask is about: every ask has
one subject category (Crop, Weather, Market, ...). That is the place to decide
which category's rules a turn needs.

## 2. Decision Drivers

1. The general prompt stays general. A category's rules are read only on a
   turn about that category.
2. Intent decides what a turn is about, once; later stages do not guess again.
3. A category's rules are text, so a reviewer edits them without a code
   change, per language, as ADR-0018 set up.
4. No code of ours joins rule text into a prompt.
5. A broken config refuses to boot (ADR-0018).

## 3. Considered Options

1. **Guidance files in the prompt registry, joined into the prompt.**
   `configs/prompts.yaml` maps an intent category to a guidance file, per
   component and language, and the turn's guidance fills a placeholder in
   the component's template.
2. **`{% if %}` branches in the template.** Every category's text stays inside
   the general prompt file, so the file grows with each category and the
   reviewer still reads them all.
3. **Rules in code**, in the planner or the composer, for a known category or
   data type. The generic flow gains a branch per category, a reviewer cannot
   change the rules, and they skip the per-language mechanism.
4. **A slot in the planner's existing skill**, filled like option 1's
   placeholder. Every skill file becomes a template, and the registry needs
   code to fill and check it.
5. **Capability tools: an Agent Skill per category, chosen by intent.** The
   planner is offered the category's skill through the Agent Skills
   capability of `pydantic-ai-harness` (`Skills`), and loads it when it needs
   it.

## 4. Decision Outcome

Chosen option: **5**. Options 1, 3 and 4 were each built during #242; review
asked for no category branch in code, for the planner's rules to load as a
skill rather than be joined into its prompt by our code, and for the composer
to carry no category rules at all.

- **Layout.** Capability tools live in their own folder, `capability_tools/`
  at the repo root, or the one `DSS_CAPABILITY_TOOLS_DIR` names: one folder
  per language, each holding one `<category in lowercase>/SKILL.md` per
  intent category (`capability_tools/en/market/SKILL.md`). A language with no
  folder uses English's. A set path with no folder, a folder named for no
  language, or a skill named for no intent category refuses to boot.
- **Port and service.** `ports/capability_tools.py::CapabilityToolProvider`
  and `config/capability_tools_service.py`, apart from the prompt registry:
  the registry serves prompt text, this serves skills the model loads.
  `get_capability_tools(lang, categories)` names the skills for the turn's
  categories, or `None`.
- **Planner.** It is built with `Skills(..., include=those names)`. The model
  sees each offered skill's name and description, and loads its rules with
  `load_capability`; its own skill tells it to load a listed one before
  `select`. The folder is read through a read-only workspace of its own, so
  the planner gets no file tools. The Market skill tells the planner which
  days to ask prices for, and that other markets' prices in the district
  still answer the ask, so it keeps them.
- **Today's date.** The planner's prompt carries today's date, so a skill
  can count days from it. It is read in `DSS_LOCAL_TIMEZONE`, Asia/Kolkata by
  default: the providers date their data in India time, and the UTC date is
  a day behind it from 00:00 to 05:30 IST.
- **Composer.** It gets no category rules. One general rule in its prompt
  covers what a mandi reply needs: the data may cover more places or days
  than asked, so give the asked place's latest figure, or say it had none and
  give each other place's latest, naming the place and date of every figure.

## 5. Consequences

- **Good:** a turn about weather is not offered the Market skill. Adding a
  category's rules for the planner is a `SKILL.md` per language, with no
  config line and no code.
- **Good:** the planner's and the composer's code stay generic. A category's
  rules change text, never the flow.
- **Bad:** following a category's rules is up to the model, not code. Picking
  the asked market's latest price is not guaranteed, and the composer reads
  every market's rows for those days, which costs more tokens.
- **Bad:** the planner reads a category's rules only if it loads the skill.
  A turn where it does not gets no Market rules, and one where it does costs
  one more model round trip.
- **Bad:** the composer's rule is general, so it is read on every turn, and a
  category that needs composer rules of its own has no place for them yet.
- **Bad:** skills are chosen by category, not by what was found. A Market
  ask that ends with no data is still offered the Market skill.
- **Bad:** a new dependency. `pydantic-ai-harness` pins one exact
  `pydantic-ai-slim` version per release (0.54.0 pins 2.54.0), so the two
  are upgraded together. It is pre-1.0, and its API may change between minor
  releases.
- **Neutral:** two kinds of skill now sit beside each other: the planner's
  own (`skills:` in `configs/prompts.yaml`, always in its prompt, binding its
  tools) and capability tools (`capability_tools/`, loaded on demand,
  instructions only).
- **Neutral:** the one category-specific line of code left is the MandiPrice
  validity window. See §6.

## 6. What Stays in Code

A category's rules are text, chosen by intent's category. The planner's and
the composer's code carry no branch for a category or a data type.

One exception stays, and it is not a rule about answers. Agmarknet refuses
a MandiPrice `/select` without a validity window. The pack lists `validity`
as a field a caller may send, so the planner sets it, and the Market skill
says which days: a named period as asked, the week up to a named day, and the
week up to today when no day is named or it is after today.

The model may still leave the window out, so
`core/planner/resource_attributes.py` adds the week up to today when the
capability called is `openagrinet:MandiPrice` and no window was sent. It is
keyed on the data type, not the category, because it is a fact about that
endpoint: another Market resource without the rule must not get it.
