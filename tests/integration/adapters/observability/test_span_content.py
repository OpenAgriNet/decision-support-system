"""Tier 3 — a real span carries no message content.

The tier-1 tests pin `include_content=False`. This pins what that produces: a
span from a real Pydantic AI agent run, inspected for the query it was given.

Worth having separately, because the setting and the outcome are two different
claims. `include_content` could keep its name and stop suppressing anything,
and only a test that reads the span would notice — which is exactly the case
`DSS_ARCHITECTURE.md` §6.1 exists to prevent.
"""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai.agent import Agent
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from dss.adapters.observability.tracing import instrumentation_settings

# A query of the shape the DSS receives, with the kind of detail a farmer
# volunteers unprompted.
_QUERY = "My name is Ramesh, phone 9876543210, wheat price at Anand mandi?"
_ANSWER = "Wheat at Anand is 2550 INR per quintal."


def _answers(_messages, _info) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=_ANSWER)])


@pytest.fixture
def spans() -> InMemorySpanExporter:
    """Collect spans in memory rather than exporting them anywhere.

    Its own `TracerProvider`, passed to the agent's settings rather than set
    globally: `trace.set_tracer_provider` is one-shot per process, so a global
    one would make this test order-dependent and leak into every other.
    """

    return InMemorySpanExporter()


def _instrument_all(exporter: InMemorySpanExporter, monkeypatch) -> None:
    """Instrument every agent, as `configure_tracing` does.

    `instrument_all` is the only public route and it sets a class-level
    default, so `monkeypatch.setattr` restores it afterwards rather than
    leaving every later test instrumented.
    """

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    settings = instrumentation_settings(tracer_provider=provider)
    monkeypatch.setattr(Agent, "_instrument_default", settings)


async def test_a_span_does_not_carry_the_query_or_the_answer(
    spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neither the farmer's words nor the composed answer reach a span.

    Asserted over every attribute of every span, not the two that hold
    messages today: Pydantic AI decides which attributes exist, and a version
    that adds a third would otherwise slip past.
    """

    monkeypatch.delenv("DSS_TRACE_INCLUDE_MESSAGE_CONTENT", raising=False)
    _instrument_all(spans, monkeypatch)
    agent = Agent(FunctionModel(_answers))

    await agent.run(_QUERY)

    recorded = spans.get_finished_spans()
    assert recorded, "instrumentation produced no spans at all"
    for span in recorded:
        for name, value in (span.attributes or {}).items():
            assert "Ramesh" not in str(value), f"{span.name}.{name}"
            assert "9876543210" not in str(value), f"{span.name}.{name}"
            assert _ANSWER not in str(value), f"{span.name}.{name}"


async def test_a_span_still_says_what_ran(
    spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Suppressing content must not leave the trace useless.

    Token counts and the model name are why tracing is wired at all — an
    operator asking "which agent was slow, and how much did it cost" needs
    them, and neither is personal data.
    """

    monkeypatch.delenv("DSS_TRACE_INCLUDE_MESSAGE_CONTENT", raising=False)
    _instrument_all(spans, monkeypatch)
    agent = Agent(FunctionModel(_answers))

    await agent.run(_QUERY)

    attributes: dict[str, object] = {}
    for span in spans.get_finished_spans():
        attributes.update(span.attributes or {})

    assert attributes.get("gen_ai.usage.input_tokens")
    assert attributes.get("gen_ai.usage.output_tokens")
    assert attributes.get("gen_ai.request.model")
