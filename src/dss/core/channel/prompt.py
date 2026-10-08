"""What the composer asks the model — the prompt, and the evidence it rests on.

Its own module rather than living inside `core/stream_response/service.py`
because the question put to the model and the mechanics of receiving the answer
are separate concerns: this one is pure string assembly, testable without an
`LLMProvider` at all, and it is where a change to what the farmer's answer is
grounded in belongs.

The system prompt's *text* lives under ``prompts/composer/``, one version per
language (configs/prompts.yaml), served through the ``PromptProvider`` port;
this module decides what the template is told — the identity and the turn's
target language. The evidence layout below stays code: it is structure the
model reads, built from network-supplied values, not prose a language reviewer
rewrites.

Framework-free: plain strings in, plain strings out. Nothing here knows the
answer arrives in pieces.
"""

from __future__ import annotations

import json

from dss.core.intent.models import Intent, ResolvedPlace
from dss.core.planner.markers import (
    CONVERSATION,
    QUESTION,
    RETRIEVED_DATA,
    wrap_as_data,
)
from dss.core.planner.models import Evidence, Failure, Identity
from dss.core.shared.models import UserTurn
from dss.ports.prompts import PromptProvider

# A reply like "2" means nothing without the question it answers. Three
# messages hold that question; the whole chat would make every answer slower.
_HISTORY_WINDOW = 3


def system_prompt(
    identity: Identity, *, turn: UserTurn, prompts: PromptProvider
) -> str:
    return prompts.get_prompt(
        "COMPOSER",
        lang=turn.target_lang,
        kwargs={
            "name": identity.name,
            "persona": identity.persona,
            "boundaries": identity.boundaries,
            "target_lang": turn.target_lang,
        },
    )


def user_prompt(evidence: Evidence, intent: Intent, *, turn: UserTurn) -> str:
    """The question and the provider's values, wrapped as data.

    A provider's text is third-party and must never read as instructions, and
    `wrap_as_data` stops either block from closing early. The place label on
    each block rides inside this same wrapped block — still content, not
    instruction, even though it traces back to the farmer's own words.
    """

    question = (
        wrap_as_data(turn.enriched_query, QUESTION)
        + "\n\n"
        + wrap_as_data(render_evidence(evidence, intent), RETRIEVED_DATA)
    )
    recent = turn.history[-_HISTORY_WINDOW:]
    if not recent:
        return question
    lines = "\n".join(f"{message.role}: {message.text}" for message in recent)
    return wrap_as_data(lines, CONVERSATION) + "\n\n" + question


def _place_label(ask_index: int, intent: Intent) -> str:
    """ "— about <place>, <place above it>", or nothing if the ask has no
    resolved place or the place has no name (a device point the turn sent
    without an area). The place above is what tells two Rampurs apart."""

    place = intent.asks[ask_index].place
    if not isinstance(place, ResolvedPlace) or not place.name:
        return ""
    above = f", {place.within[-1]}" if place.within else ""
    return f" — about {place.name}{above}"


def render_evidence(evidence: Evidence, intent: Intent) -> str:
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

    Each heading also carries the place its question's ask resolved, so a
    two-place turn ("wheat price in Pune, will it rain in Anand") labels each
    question with its own place. It is a fallback the model uses only when
    the data itself names no place.
    """

    name_by_id = {source.id: source.name for source in evidence.sources}
    by_question: dict[int, dict[str, list[dict]]] = {}
    for result in evidence.results:
        by_question.setdefault(result.ask_index, {}).setdefault(
            result.source_id, []
        ).append(result.data)

    blocks: list[str] = []
    for position, (ask_index, sources) in enumerate(by_question.items(), start=1):
        label = _place_label(ask_index, intent)
        for source_id, payloads in sources.items():
            name = name_by_id.get(source_id, "unknown")
            body = "\n\n".join(
                json.dumps(payload, indent=2, ensure_ascii=False)
                for payload in payloads
            )
            blocks.append(f"Question {position} — [{source_id}] {name}{label}\n{body}")
    blocks.extend(_render_failures(evidence, intent))

    if not blocks:
        return "Nothing was retrieved."
    return "\n\n".join(blocks)


def _render_failures(evidence: Evidence, intent: Intent) -> list[str]:
    """Calls that did not answer, and why.

    "We could not reach Agmarknet" and "nobody serves this" are the same
    empty result but very different things to tell a farmer. Without this the
    composer saw only ``results`` and rendered both as "Nothing was
    retrieved.", which is the confusion ``Evidence.failed`` was added to
    prevent.
    """

    return [
        f"{_failure_lead(failure)}"
        f"{_place_label(failure.ask_index, intent)}: {failure.reason}"
        for failure in evidence.failed
    ]


def _failure_lead(failure: Failure) -> str:
    # No capability means no call was made: the ask's place failed. Blaming a
    # provider would have the composer tell the farmer a service is down.
    if failure.capability is None:
        return "Nothing searched"
    return f"Could not reach a provider for {failure.capability}"
