"""Tier 3 — the stage spans nest correctly across a whole turn.

Each piece is unit-tested on its own. What only a real turn shows is the
parenting: intent, enrichment and discovery run in one anyio task, moderation
in a sibling task, and both are started from a task group inside `run_turn`.
Anyio copies the OpenTelemetry context into each child task, so the two
branches should both land under `dss.turn` — but that is a property of how the
task group and the context interact, not of any line in this repo, and nothing
below the integration level can prove it.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from dss.adapters.invocation.client import HttpCapabilityInvocation
from dss.adapters.observability.tracing import open_span
from dss.core.provider_discovery.models import ProviderCapability
from dss.observability.trace_log import set_stage_span_opener

from .test_orchestrator import (
    _ANSWERED_EVIDENCE,
    _build,
    _collect,
    _FakeCompose,
    _one_ask,
    _served_discovery,
)

_CAPABILITY = ProviderCapability(
    provider_id="agmarknet",
    provider_name="Agmarknet",
    capability="openagrinet:MandiPrice",
    resource_id="res:agmarknet:mandi-price",
    observed_categories=("Market",),
)

_ON_SELECT = {
    "message": {
        "contract": {
            "commitments": [
                {
                    "resources": [
                        {
                            "id": "res:agmarknet:mandi-price:2026-08-26",
                            "resourceAttributes": {
                                "@type": _CAPABILITY.capability,
                                "parameters": [],
                            },
                        }
                    ]
                }
            ]
        }
    }
}


class _PlanThatCallsSelect:
    """Stands in for the planner, and makes one real `/select` call.

    The point of this test is depth. `dss.select` is opened inside the
    invocation adapter, which the real planner reaches through a Pydantic AI
    tool — several frames and an agent run below the stage span. A fake that
    only returned evidence would leave that whole path unexercised, and the
    nesting it has to prove untested.
    """

    def __init__(self, evidence) -> None:  # noqa: ANN001
        self._evidence = evidence

    async def __call__(self, turn, *, intent, discovery, verdict):  # noqa: ANN001
        def answers(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_ON_SELECT)

        async with httpx.AsyncClient(transport=httpx.MockTransport(answers)) as client:
            invocation = HttpCapabilityInvocation(
                client=client,
                base_url="https://provider-network.example",
                sender_id="seeker.example",
                receiver_id="provider.example",
            )
            await invocation.select(_CAPABILITY, {}, "t1")
        return self._evidence


@pytest.fixture
def spans(monkeypatch) -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr("opentelemetry.trace.get_tracer_provider", lambda: provider)
    set_stage_span_opener(open_span)
    yield exporter
    set_stage_span_opener(None)


async def test_every_stage_hangs_off_the_turn(spans) -> None:
    orch, _ = _build(
        intent=_one_ask(),
        discovery=_served_discovery(),
        plan=_PlanThatCallsSelect(_ANSWERED_EVIDENCE),
        compose=_FakeCompose("Wheat is 2,275 Rs [1]."),
    )

    await _collect(orch)

    finished = {s.name: s for s in spans.get_finished_spans()}
    turn = finished["dss.turn"]

    assert finished.keys() >= {
        "dss.stage.intent",
        "dss.stage.enrichment",
        "dss.stage.discovery",
        "dss.stage.moderation",
        "dss.stage.planner",
        "dss.stage.composer",
    }
    # Moderation is the sibling task. If the context did not cross the task
    # group it would parent to nothing and start a second trace of its own.
    assert finished["dss.stage.moderation"].parent.span_id == turn.context.span_id
    assert finished["dss.stage.intent"].parent.span_id == turn.context.span_id


async def test_a_provider_call_nests_under_the_planner_stage(spans) -> None:
    """The deepest nesting this story creates, and the one most likely to
    break: a `/select` span is opened several frames below the stage span that
    should own it. If it parented to the turn instead, the trace would say the
    call happened beside the planner rather than because of it."""

    orch, _ = _build(
        intent=_one_ask(),
        discovery=_served_discovery(),
        plan=_PlanThatCallsSelect(_ANSWERED_EVIDENCE),
        compose=_FakeCompose("Wheat is 2,275 Rs [1]."),
    )

    await _collect(orch)

    finished = {s.name: s for s in spans.get_finished_spans()}

    assert (
        finished["dss.select"].parent.span_id
        == finished["dss.stage.planner"].context.span_id
    )
    assert (
        finished["dss.select.attempt"].parent.span_id
        == finished["dss.select"].context.span_id
    )
