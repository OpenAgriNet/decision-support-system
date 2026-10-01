"""What the composer asks the model — the prompt, and the evidence it rests on.

Its own module rather than living inside `core/stream_response/service.py`
because the question put to the model and the mechanics of receiving the answer
are separate concerns: this one is pure string assembly, testable without an
`LLMProvider` at all, and it is where a change to what the farmer's answer is
grounded in belongs.

Framework-free: plain strings in, plain strings out. Nothing here knows the
answer arrives in pieces.
"""

from __future__ import annotations

import json

from dss.core.planner.markers import (
    QUESTION,
    RETRIEVED_DATA,
    wrap_as_data,
)
from dss.core.planner.models import Evidence, Identity
from dss.core.shared.models import UserTurn

SYSTEM_PROMPT = """You are {name}, {persona}

{boundaries}

Write the answer a farmer will read. Use only the retrieved data below —
never your own knowledge, and never a number the data does not contain.

- Answer the question that was asked, leading with what it asked for.
- Plain words, short sentences. A farmer is reading this, not an analyst.
- Answer in sentences. Do not lay values out as a list or a table.
- Never use the data's own field names as labels. They are schema terms, not
  what a farmer calls things — say what the value means instead.
- When one thing carries several prices, the usual price is the answer. Give
  that, then say how low and how high it went in the same sentence.
- Cite a source with its number in square brackets, like [1].
- Answer each question from one source only. Where several sources answer
  the same question, read them, pick the single one that answers it best,
  and write from that one alone — do not stitch two sources' text together
  and do not average their numbers. Cite only the source you used.
- Different questions may be answered by different sources. The one-source
  rule is per question, not per reply.
- Reply in {target_lang}.

If the data does not answer the question, say plainly that you could not
find it. Do not fill the gap with a guess — a wrong price costs a farmer
money.

If a line says a provider could not be reached, say the information was not
available right now and could be worth asking again. That is different from
nobody having the answer — do not turn one into the other.
"""


def system_prompt(identity: Identity, *, turn: UserTurn) -> str:
    return SYSTEM_PROMPT.format(
        name=identity.name,
        persona=identity.persona,
        boundaries=identity.boundaries,
        target_lang=turn.target_lang,
    )


def user_prompt(evidence: Evidence, *, turn: UserTurn) -> str:
    """The question and the provider's values, wrapped as data.

    A provider's text is third-party and must never read as instructions, and
    `wrap_as_data` stops either block from closing early.
    """

    return (
        wrap_as_data(turn.enriched_query, QUESTION)
        + "\n\n"
        + wrap_as_data(render_evidence(evidence), RETRIEVED_DATA)
    )


def render_evidence(evidence: Evidence) -> str:
    """Lay out the sources and their values for the model.

    ``Result.data`` is a provider's ``resourceAttributes`` verbatim — each
    pack has its own shape, so this does not try to interpret them. Turning
    ``{"prices": {"modal": 2200}}`` into "2,200 Rs" is the model's job; this
    only has to make the values legible and say which source each came from.

    Grouped by question, then by source, because the composer is told to
    answer each question from one source alone. Flat, one block per result,
    the layout could not show that: a document that returned four passages
    looked like four separate documents agreeing with each other, and two
    documents' passages sat interleaved with nothing to say where one ended.
    """

    name_by_id = {source.id: source.name for source in evidence.sources}
    by_question: dict[int, dict[str, list[dict]]] = {}
    for result in evidence.results:
        by_question.setdefault(result.ask_index, {}).setdefault(
            result.source_id, []
        ).append(result.data)

    blocks: list[str] = []
    for position, sources in enumerate(by_question.values(), start=1):
        for source_id, payloads in sources.items():
            name = name_by_id.get(source_id, "unknown")
            body = "\n\n".join(
                json.dumps(payload, indent=2, ensure_ascii=False)
                for payload in payloads
            )
            blocks.append(f"Question {position} — [{source_id}] {name}\n{body}")
    blocks.extend(_render_failures(evidence))

    if not blocks:
        return "Nothing was retrieved."
    return "\n\n".join(blocks)


def _render_failures(evidence: Evidence) -> list[str]:
    """Calls that did not answer, and why.

    "We could not reach Agmarknet" and "nobody serves this" are the same
    empty result but very different things to tell a farmer. Without this the
    composer saw only ``results`` and rendered both as "Nothing was
    retrieved.", which is the confusion ``Evidence.failed`` was added to
    prevent.
    """

    return [
        f"Could not reach a provider for {failure.capability}: {failure.reason}"
        for failure in evidence.failed
    ]
