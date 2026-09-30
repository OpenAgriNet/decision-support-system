# ADR-0015: Ask Which Place, and Read the Reply, Statelessly

- **Status:** ACCEPTED
- **Date:** 2026-09-30
- **Deciders:** DSS implementation

---

## 1. Context and Problem Statement

A name like Rampur can mean several places. ADR-0014 asks the farmer which
one, with a numbered list. The farmer replies "Himachal" or "2". Today that
reply is a new question and means nothing.

The reply must finish the first question. The DSS keeps no session.

## 2. Decision Drivers

1. No stored state.
2. No extra model call per turn.
3. Never a silent guess.
4. Works in any country. The question never says "state" or "district".
5. The farmer can reply loosely: a name, a number, a short form.

## 3. Considered Options

1. **Stateless.** The history already holds our question. The model reads it
   and copies the picked line. Code checks the copy.
2. **Store the pending question** per session.
3. **Hide a tag** in our reply.
4. **A second model call** that reads only the reply.

## 4. Decision Outcome

Chosen option: **1.** The Experience API already sends the history. The
history is the memory.

Option 2 needs an expiry and a store. Option 3 breaks if the tag is stripped.
Option 4 adds a call to every turn, and most turns are not replies.

- **Our question is fixed text, not translated.** Each line reads "Rampur,
  Himachal Pradesh", so the model can copy it exactly.
- **The model copies the picked line** as the place name. The intent prompt
  has one worked example of this, and no rule. A rule saying a listed place
  can be the farmer's own made a model carry a place only the assistant had
  named.
- **Code splits at the last comma**, looks up the name, and keeps the match
  whose parent places hold the second part.
- **Still not one place? Ask the same question again.** Never guess.
- **At most 5 choices.** The farmer's region narrows first. If more than 5
  remain, list the groups one level up. The level is never named. Past 5
  groups, add "Not in this list? Tell me the area it is in." The limit is the
  setting `max_choices`.
- **No expiry.** A new question is new. A late "2" still answers the old one.
- **The answer names the place it used**, such as "Rampur, Himachal Pradesh",
  for every resolved place. This avoids a new flag for "picked from several".
- **A partly answered turn adds the question** after the answer.

The pattern may be reused: a fixed list, the model copies the pick, code
checks it. It is place-only for now.

## 5. Consequences

- The Experience API must send our message back unchanged. To confirm with
  that team.
- The model may copy a line wrongly. The code then asks again, so the farmer
  is never given a wrong place. The live-model tests (tier 5) measure how
  often this happens, on each model in use.
- The prompt that reads the question is fragile. One added sentence once made
  a model merge two separate questions into one (ADR-0014). After any change
  to that prompt, run all the live-model tests on each model in use, not only
  the new ones.
- A partly answered turn streams one more claim, and it is in the finished
  content.
- Every place label is longer ("Pune, Maharashtra").
