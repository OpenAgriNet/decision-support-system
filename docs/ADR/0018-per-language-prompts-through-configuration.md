# ADR-0018: Per-Language Prompts Through Configuration

- **Status:** ACCEPTED
- **Date:** 2026-10-07 (extended 2026-10-08 with system-text localization)
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

Language has a second surface besides the model-facing prompts: some replies
the DSS writes itself, with no model in the loop — moderation refusals
(`core/moderation/messages.py`), clarification questions and the no-provider
answers (`core/channel/service.py`, wording in `clarification-text.yaml`). All
of it is English, so a Hindi farmer whose turn is refused is refused in a
language they may not read. This ADR covers both surfaces.

## 2. Decision Drivers

1. A deployment gives a language its own version of any prompt through
   configuration only.
2. A missing version never breaks an answer. English is always there.
3. The team can see which languages use which prompt versions.
4. The language set is open-ended — this is a DPG, so it must scale by
   configuration, not by code (multilingual epic).
5. `core/` reads no files (the isolation rule the hexagon rests on).
6. A farmer reads every reply in their `target_lang`, refusals and
   clarifications included — with no per-language text to hand-maintain.
7. The system-text paths are deliberately model-free today; English must stay
   free, and a language problem must never cost the farmer an answer.
8. ADR-0017's follow-up mechanism: the ambiguous-place question's numbered
   options are copied back verbatim by a later turn and looked up in an
   English index. Translated options break follow-ups silently.

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

For the system-authored text (drivers 6–8):

5. **Localize at the orchestrator seam.** Keep the English strings as the one
   reviewed vocabulary; one short model call on the composer's binding renders
   the message in `target_lang` just before it leaves. English skips the call;
   failure falls back to English.
6. **Per-language YAML text** — a `clarification-text.hi.yaml` per language.
   Every string × every language is hand-written and reviewed; driver 4 says
   the set is open-ended, and the ambiguous-place question is *built*, not a
   fixed string, so a YAML can never hold it.
7. **Let the composer LLM write refusals from scratch** per turn. The refusal
   wording stops being a reviewed vocabulary — a safety property for
   moderation text — and the model-free paths all gain a mandatory model call.
8. **Leave it to the Experience Layer.** §5.1 already places *translation*
   outside the DSS, but the Experience Layer cannot tell a refusal from an
   answer in the stream, and the epic scopes localising system-authored text
   to the DSS.

## 4. Decision Outcome

Chosen options: **1** (the registry) for the prompts, and **5** (localize at
the orchestrator seam) for the system-authored text.

- **Layout.** `prompts/<component>/<lang>/prompt.j2`, one file per component
  (intent, moderation, planner, composer, localizer) per language.
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
- **Hindi ships as a demo** for every component, marked as a demo in each
  file and pending a Hindi speaker's review. It proves the mechanism end to
  end; it is not backed by an eval. Which languages get a real override is
  the M3 eval's decision.

**System text is localized on the way out** (extension, 2026-10-08):

- **The seam.** `core/channel/localize.py::build_localizer` binds the
  composer's model and the prompt registry once; `Components.localize` hands
  the callable to the orchestrator, which routes its four system-text exits
  through it: the moderation refusal, the no-place clarification, the
  nobody-serves answer, and the ambiguous-place question appended to a
  composed answer. Composed answers themselves never pass through — the
  composer already writes in `target_lang` (#158).
- **The call.** `structured` with a one-field schema (`LocalizedText`), so
  the adapter's retries apply and the model cannot wrap the message in
  preamble. The message travels in the user slot; the system prompt is the
  `localizer` entry in the prompt registry — per-language overridable like
  every other prompt above, with a marked Hindi demo.
- **English costs nothing.** Any `en-*` target returns the text untouched,
  no model call (driver 7).
- **Fail open, per message.** A failed or empty rendering serves the English
  original (driver 7); the adapter's logging records the failure. English has
  no further fallback, so a failure there propagates as the defect it is.
- **Verbatim rules in the prompt.** Place names, scheme names, numbers,
  units, codes and every numbered option line stay exactly as written; only
  the sentence around them is rendered (driver 8). The follow-up then works
  as ADR-0017 designed it: the option line in the history is still the
  English string the area index can resolve.
- **One vocabulary to review.** The English strings in `messages.py` and
  `clarification-text.yaml` remain the single source of what the DSS says;
  localization changes the language, never the message.

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

From the system-text extension:

- Good: a refused or asked-back farmer reads the DSS in their own language,
  in every language, with nothing per-language to maintain — and nothing
  changes for English deployments: same text, same zero model calls on these
  paths.
- Trade-off: a non-English refusal/clarification now costs one short model
  call and its latency on paths that had none. Bounded: one call per block,
  on the composer's existing binding and knobs.
- Trade-off: the rendered wording is model output, not a reviewed string.
  The reviewed English meaning is pinned; the per-language phrasing is the
  model's, and judging it is the M3 eval's job — same stance as answers.
- Risk accepted: when moderation rejects because the *moderation* model is
  down (`MODERATION_UNAVAILABLE`), the localizer call may fail too — and then
  falls back to the English message, which is the designed floor.
