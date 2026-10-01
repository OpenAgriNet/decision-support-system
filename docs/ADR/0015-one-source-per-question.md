# ADR-0015: Answer each question from one source

- **Status:** ACCEPTED
- **Date:** 2026-09-30
- **Deciders:** DSS implementation
- **Consulted:** —

---

## 1. Context and Problem Statement

One `/select` call can come back with several passages, and those passages are
often not from the same document. A farmer asked how to grow masoor dal. One
provider answered with four passages: two from a pulses policy paper and two
from an ICAR advisory booklet.

Until now the composer was handed all of them at once and told to write an
answer. Nothing stopped it from taking a sentence from one document and a
sentence from another and joining them into one piece of advice.

That is a real risk, not a tidiness complaint. Two documents can disagree. They
can be written for different states, different seasons, or different varieties.
A sowing depth that is right in one booklet is wrong once it is pasted next to
another booklet's spacing. The joined answer looks confident and cites both, and
a farmer has no way to see that no single document ever said it.

The layout the composer read made this worse. Every passage was printed with its
own heading, so one document that returned four passages looked like four
separate documents that happened to agree.

## 2. Decision Drivers

- **A farmer must be able to check the answer.** An answer that exists in no
  single document cannot be checked against one.
- **Two sources can conflict without saying so.** Nothing in the data marks two
  passages as written for the same place, season or variety.
- **A turn can ask more than one thing.** "What is the onion price and will it
  rain tomorrow" needs two providers. A rule that allows only one source in a
  reply would drop one of the two answers.
- **Judging which source answers best needs reading.** It depends on the
  question asked, not on a count of passages or the order they arrived in.

## 3. Considered Options

1. **Leave it as it was.** The composer sees everything and may blend.
2. **Pick the source in code.** Group the passages by document and keep one,
   using a fixed rule such as "the document that returned the most passages".
3. **Let the composer pick, and tell it the rule.** Show it the passages
   grouped by question and by document, and instruct it to use one document per
   question.

## 4. Decision Outcome

**Option 3.** The composer picks, guided by a rule in its prompt, and the rule
applies per question rather than per reply.

The composer picks because picking well means reading the passages against the
question that was asked. A code rule can only count passages or take the first
one, and the document with the most passages is not the one that answers best.

The rule is per question because a turn can ask several things and be served by
several providers. Each question is answered from one document; a reply covering
two questions may name two.

Two changes make this work:

- **The prompt states the rule.** Answer each question from one source. Where
  several answer it, read them, pick the one that answers best, write from that
  one alone, and cite only it. Do not join two sources' text and do not average
  their numbers.
- **The layout shows the groups.** Evidence is laid out by question, and inside
  a question by document, with a document's passages gathered under one heading.
  The composer cannot follow a rule about documents if it cannot see where one
  document ends.
- **The reply lists only what was cited.** The documents the composer read and
  rejected are left out. Listing them claims a provenance the answer does not
  have, and a farmer who opens one finds a document the advice never came from.
  A number naming no listed document is dropped, and prose that cites nothing
  lists nothing — there is no way to tell what it rested on.

## 5. Consequences

**Good**

- Every answer traces back to one document that a farmer can be pointed to.
- A document that returned four passages now reads as one source, not as four
  sources agreeing.
- Multi-question turns keep working, and each question still names its own
  source.

**Bad**

- Corroboration is lost. Two documents saying the same thing no longer
  strengthen the answer, because only one is used.
- A question is sometimes answered best by combining two documents — one giving
  the variety, another the spacing. That answer is no longer available, and the
  farmer gets the better half instead of the whole.
- The rule is a prompt instruction, so it is followed rather than enforced. A
  model can ignore it. Nothing in the code rejects an answer that cites two
  sources for one question.
- An answer that cites nothing now carries no sources at all, where it used to
  carry every source consulted. That is honest but it is a loss: a model that
  forgets to cite leaves the farmer with no document to check.

**Follow-up**

- If the rule turns out to be ignored often, the check belongs in the response
  reviewer, which can see the finished answer and its citations. That is a
  separate decision and is not taken here.
