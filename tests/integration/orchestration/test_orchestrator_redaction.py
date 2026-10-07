"""Tier 3 — redaction is the first step of a turn.

The real orchestrator and the real `redact_texts`, with fake identifiers behind
the port. What is pinned: every stage after redaction sees tags, the audit
record holds tags, the planner is handed the map, and the farmer reads their
own values back.
"""

from __future__ import annotations

from functools import partial

from dss.core.intent.models import Intent
from dss.core.redaction.models import PiiSpan, RedactionPolicy, ValueHandling
from dss.core.shared.models import (
    Claim,
    ClaimDelta,
    ConversationMessage,
    TurnFinished,
    UserDetails,
    UserTurn,
)
from dss.orchestration.redaction import pass_through, redact_texts

from .test_orchestrator import (
    _ANSWERED_EVIDENCE,
    _PUNE,
    _build,
    _ctx,
    _FakeCompose,
    _FakeModerationLLM,
    _FakePlan,
    _one_ask,
    _served_discovery,
)

PHONE = "9876543210"
AADHAAR = "234123412346"

POLICY = RedactionPolicy(
    entities={"phone": ValueHandling.KEEP, "aadhaar": ValueHandling.DESTROY}
)


class _FindsValues:
    """Finds fixed values wherever they appear — a stand-in for the port."""

    def __init__(self, name: str = "fake", **values: str) -> None:
        self.name = name
        self._values = values  # entity → value

    async def identify(self, text: str) -> list[PiiSpan]:
        spans = []
        for entity, value in self._values.items():
            start = text.find(value)
            while start >= 0:
                spans.append(
                    PiiSpan(start, start + len(value), entity, 1.0, self.name, value)
                )
                start = text.find(value, start + 1)
        return spans


class _Breaks:
    name = "broken"

    async def identify(self, text: str) -> list[PiiSpan]:
        raise RuntimeError(f"model crashed on {text}")


class _RecordingIntentLLM:
    def __init__(self, result: Intent) -> None:
        self._result = result
        self.seen: list[str] = []

    async def structured(self, *, system_prompt, user_query, schema):  # noqa: ANN001
        self.seen.append(system_prompt + user_query)
        return self._result


class _RecordingModerationLLM(_FakeModerationLLM):
    def __init__(self) -> None:
        super().__init__()
        self.seen: list[str] = []

    async def structured(self, *, system_prompt, user_query, schema):  # noqa: ANN001
        self.seen.append(system_prompt + user_query)
        return await super().structured(
            system_prompt=system_prompt, user_query=user_query, schema=schema
        )


def _turn(query: str) -> UserTurn:
    return UserTurn(
        original_query=query,
        enriched_query=query,
        session_id="0b6f3c1e-2f4a-4d7e-9a51-3c2d8e7f6a10",
        transaction_id="5d1e9a7c-8b3f-4c2a-b6d4-7e9f0a1b2c3d",
        source_lang="en",
        target_lang="en",
        channel="web",
        location=_PUNE,
        history=[ConversationMessage(role="user", text=f"earlier I gave {PHONE}")],
    )


def _redact(*identifiers):  # noqa: ANN002
    return partial(redact_texts, identifiers=list(identifiers), policy=POLICY)


async def _run(*, redact, compose=None, intent_llm=None, moderation_llm=None):  # noqa: ANN001
    plan = _FakePlan(_ANSWERED_EVIDENCE)
    compose = compose or _FakeCompose("We will message «phone_1» today [1].")
    orch, turns = _build(
        intent=_one_ask(),
        discovery=_served_discovery(),
        plan=plan,
        compose=compose,
        redact=redact,
        intent_llm=intent_llm,
        moderation_llm=moderation_llm,
    )
    query = f"status of my application, number {PHONE}, aadhaar {AADHAAR}"
    events = [event async for event in orch.run(_turn(query), _ctx())]
    return events, plan, compose, turns


async def test_no_stage_after_redaction_sees_a_real_value() -> None:
    intent_llm = _RecordingIntentLLM(_one_ask())
    moderation_llm = _RecordingModerationLLM()

    _, plan, compose, _ = await _run(
        redact=_redact(_FindsValues(phone=PHONE, aadhaar=AADHAAR)),
        intent_llm=intent_llm,
        moderation_llm=moderation_llm,
    )

    for prompt in intent_llm.seen + moderation_llm.seen:
        assert PHONE not in prompt and AADHAAR not in prompt
    for turn in (plan.turn, compose.turn):
        assert "«phone_1»" in turn.original_query
        assert "«aadhaar_1»" in turn.original_query
        assert PHONE not in turn.model_dump_json()
        # One value, one tag — in the question and in the history.
        assert turn.history[0].text == "earlier I gave «phone_1»"


async def test_the_audit_record_holds_tags_only() -> None:
    _, _, _, turns = await _run(
        redact=_redact(_FindsValues(phone=PHONE, aadhaar=AADHAAR))
    )

    record = turns.records["t1"]
    assert PHONE not in record.turn.model_dump_json()
    assert AADHAAR not in record.turn.model_dump_json()
    assert PHONE not in record.finished.model_dump_json()
    assert "«phone_1»" in record.finished.content[0].text


async def test_the_planner_is_handed_the_map_with_kept_values_only() -> None:
    _, plan, _, _ = await _run(
        redact=_redact(_FindsValues(phone=PHONE, aadhaar=AADHAAR))
    )

    assert plan.reveal.values == {"«phone_1»": PHONE}


async def test_the_farmer_reads_their_own_number_back() -> None:
    compose = _FakeCompose(
        "", chunks=("We will message «pho", "ne_1» today. Your «aadhaar_1» [1].")
    )

    events, _, _, _ = await _run(
        redact=_redact(_FindsValues(phone=PHONE, aadhaar=AADHAAR)), compose=compose
    )

    streamed = "".join(e.text for e in events if isinstance(e, ClaimDelta))
    assert streamed == f"We will message {PHONE} today. Your «aadhaar_1» [1]."
    assert all("«pho" not in e.text for e in events if isinstance(e, ClaimDelta))
    claim = next(e for e in events if isinstance(e, Claim))
    assert PHONE in claim.content.text
    finished = events[-1]
    assert isinstance(finished, TurnFinished)
    assert PHONE in finished.content[0].text
    # A destroyed value has nothing to come back as.
    assert "«aadhaar_1»" in finished.content[0].text


async def test_a_failing_identifier_is_dropped_and_the_others_still_apply() -> None:
    _, plan, _, _ = await _run(redact=_redact(_Breaks(), _FindsValues(phone=PHONE)))

    assert PHONE not in plan.turn.original_query


async def test_with_redaction_off_the_turn_passes_through_unchanged() -> None:
    _, plan, _, _ = await _run(redact=pass_through)

    assert PHONE in plan.turn.original_query
    assert plan.reveal.values == {}


async def test_the_users_phone_gets_the_same_tag_as_the_question() -> None:
    plan = _FakePlan(_ANSWERED_EVIDENCE)
    orch, turns = _build(
        intent=_one_ask(),
        discovery=_served_discovery(),
        plan=plan,
        compose=_FakeCompose("Done [1]."),
        redact=_redact(_FindsValues(phone=PHONE)),
    )
    turn = _turn(f"status for {PHONE}").model_copy(
        update={"user": UserDetails(user_id="u1", phone=PHONE)}
    )

    _ = [event async for event in orch.run(turn, _ctx())]

    assert plan.turn.user.phone == "«phone_1»"
    assert plan.turn.original_query == "status for «phone_1»"
    assert PHONE not in turns.records["t1"].turn.model_dump_json()
    assert plan.reveal.values == {"«phone_1»": PHONE}
