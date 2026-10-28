"""Tier 3 — the events of a streamed answer agree with each other."""

from __future__ import annotations

from dss.core.forms.models import FormBlock, FormContext, FormDefinition
from dss.core.shared.models import (
    ClaimDelta,
    RefusalBlock,
    Source,
    SourceKind,
    TextBlock,
    TurnOutcome,
    TurnStatus,
)
from dss.orchestration.answer_stream import AnswerStream

IMD = Source(id="1", name="IMD", kind=SourceKind.PROVIDER)
AGMARKNET = Source(id="2", name="Agmarknet", kind=SourceKind.PROVIDER)
ANSWERED = TurnOutcome(status=TurnStatus.ANSWERED, confidence=92)
GRIEVANCE_FORM = FormBlock(
    form_id="8f3c",
    form=FormDefinition(
        context=FormContext(scheme="PMFBY", category="Grievance", action="support"),
        fields=(),
    ),
)


def test_a_piece_is_handed_on_as_a_delta():
    answer = AnswerStream()

    assert answer.add_piece("Rain is ") == ClaimDelta(text="Rain is ")


def test_a_claim_is_the_pieces_written_since_the_last_one():
    answer = AnswerStream()
    answer.add_piece("Rain is ")
    answer.add_piece("likely [1].")

    claim = answer.claim(TextBlock(text="ignored", source_ids=("1",)), (IMD,))

    assert claim.content == TextBlock(text="Rain is likely [1].", source_ids=("1",))
    assert claim.sources == (IMD,)


def test_a_claim_keeps_the_type_of_its_block():
    answer = AnswerStream()
    answer.add_piece("I can't help with that.")

    claim = answer.claim(RefusalBlock(text="ignored"), ())

    assert claim.content == RefusalBlock(text="I can't help with that.")


def test_each_claim_takes_only_its_own_pieces():
    answer = AnswerStream()
    answer.add_piece("Rain [1].")
    answer.claim(TextBlock(text=""), (IMD,))
    answer.add_piece("Wheat [2].")

    second = answer.claim(TextBlock(text=""), (AGMARKNET,))

    assert second.content.text == "Wheat [2]."


def test_a_claim_with_no_pieces_is_empty():
    answer = AnswerStream()

    assert answer.claim(RefusalBlock(text="ignored"), ()).content.text == ""


def test_the_finish_holds_every_claimed_block_in_order():
    answer = AnswerStream()
    answer.add_piece("Rain [1].")
    first = answer.claim(TextBlock(text=""), (IMD,))
    answer.add_piece("Wheat [2].")
    second = answer.claim(TextBlock(text=""), (IMD, AGMARKNET))

    finished = answer.finish(ANSWERED)

    assert finished.outcome == ANSWERED
    assert finished.content == (first.content, second.content)
    assert finished.sources == (IMD, AGMARKNET)


def test_written_is_what_the_next_claim_would_hold():
    answer = AnswerStream()
    answer.add_piece("Rain ")
    answer.add_piece("[1].")

    assert answer.written == "Rain [1]."


def test_a_whole_block_is_claimed_as_it_is():
    """A form has no words to stream: it is claimed whole, after the text."""

    answer = AnswerStream()
    answer.add_piece("Fill in the form below.")
    answer.claim(TextBlock(text=""), ())

    claim = answer.add_block(GRIEVANCE_FORM)

    assert claim.content == GRIEVANCE_FORM
    assert answer.finish(ANSWERED).content == (
        TextBlock(text="Fill in the form below."),
        GRIEVANCE_FORM,
    )
