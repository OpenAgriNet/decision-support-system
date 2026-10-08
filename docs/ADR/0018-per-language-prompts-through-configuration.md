# ADR-0018: Per-Language Prompts Through Configuration

- **Status:** ACCEPTED
- **Date:** 2026-10-07
- **Deciders:** DSS implementation

---

## 1. Context and Problem Statement

The DSS answers in the farmer's language, but every prompt is written in
English. The farmer's question reaches the model as typed, and the composer is
told "Reply in {target_lang}" (decision on #158). For some languages a prompt
written in that language may work better.

A language reviewer must be able to replace a prompt for one language when the
eval shows the English one falls short — without a code change, and without
risking any other language. Prompt text also lived in four different places:
Python strings in `core/intent` and `core/moderation`, a constant in
`core/channel`, and a markdown file only the planner loaded. There was no one
place to swap a prompt at all.

## 2. Decision Drivers

1. A deployment gives a language its own version of any prompt through
   configuration only.
2. A missing version never breaks an answer. English is always there.
3. The team can see which languages use which prompt versions.
4. The language set is open-ended — this is a DPG, so it must scale by
   configuration, not by code (multilingual epic).
5. `core/` reads no files (the isolation rule the hexagon rests on).

## 3. Considered Options

1. **A prompt registry.** All prompt templates live under `prompts/` as one
   file per component per language; `configs/prompts.yaml` names them; a
   prompt service loads the registry at boot and serves each component its
   prompt in the turn's `target_lang`, falling back to English.
2. **Per-language Python modules** — a `prompts_hi.py` beside each service.
   Every new language is a code change and a release; a reviewer cannot ship
   one.
3. **One template with `{% if lang == "hi" %}` branches.** Every language
   edits the same file, so a Marathi fix can break the Hindi prompt, and the
   file becomes unreadable fast.
4. **Translate prompts at runtime with a model.** Adds a model call, invents
   wording nobody reviewed, and §5.1 already rules a translation step out of
   the DSS.

## 4. Decision Outcome

Chosen option: **1**, the registry.

- **Layout.** `prompts/<component>/<lang>/prompt.j2`, one file per component
  (intent, moderation, planner, composer) per language.
  `configs/prompts.yaml` maps each component to its files and declares the
  placeholders the code supplies. Both directories sit at the repo root and
  are copied into the image; a deployment mounts its own pair and points
  `DSS_PROMPT_CONFIG_PATH` at the YAML.
- **The seam.** A new port, `ports/prompts.py::PromptProvider`, with
  `get_prompt(identifier, lang, kwargs)` and `get_skills(identifier, lang)`.
  Core services call the port like they call `LLMProvider`; the
  implementation (`config/prompt_service.py`, Jinja2) is the only thing that
  reads the files. Driver 5 holds.
- **Fallback.** Lookup is exact tag, then primary subtag (`hi-IN` → `hi`),
  then `en`. `en` is required for every component, checked at boot. The fall
  to English is recorded as a trace event (`event=language_fallback`), which
  is how the team sees where English is filling a gap (driver 3). Even an
  override that fails to render serves English rather than failing the turn.
- **Checked at boot, not mid-turn.** Every template must use all of its
  component's declared placeholders and nothing else. `str.format` dropped a
  typo'd placeholder silently (the old `_checked` guarded only the planner);
  the service now refuses to boot on it, for every component and language.
- **Skills ride with the prompt.** The planner's skill files are named per
  language in the same YAML, so the guidance and the tools stay the pair one
  reviewer shipped. A language entry without skills uses the English skills.
- **What stays code.** The dynamic sections (history window, policy list,
  asks, Direct answers wrapped as data) are built in `core/` and handed to
  the template as values — their rules are safety behaviour, not wording. A
  translation must keep code-emitted tokens verbatim: marker names, enum
  values, field names, scheme names, units, tool names.
- **Hindi ships as a demo** for all four components, marked as a demo in each
  file and pending a Hindi speaker's review. It proves the mechanism end to
  end; it is not backed by an eval. Which languages get a real override is
  the M3 eval's decision.

## 5. Consequences

- Good: a language reviewer edits one file and one YAML entry; nothing else
  moves. Deleting the entry restores today's behaviour exactly.
- Good: every prompt now has one home, one loader, one validation path, and
  one test file pinning its content
  (`tests/unit/config/test_shipped_prompts.py`).
- Good: a broken or missing override can only ever cost a trace event, never
  an answer.
- Trade-off: Jinja2 becomes a runtime dependency, and prompt text no longer
  sits beside the code that uses it — the tests that pinned prompt wording
  moved from the core tiers to the config tier with it.
- Trade-off: the English and Hindi files can drift apart in meaning. The
  boot check pins placeholders, not semantics; keeping translations honest is
  the eval's job (M3) and the reviewer's.
- `config/defaults/planner-prompt.md` and `config/defaults/skills/` are gone;
  the planner's template and skill live in the registry like everyone else's.
