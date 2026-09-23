"""Contract tests for the spans a /select call leaves behind.

The retry loop is one of the few things that can quietly turn a fast call into
a slow one, and its cost is invisible from outside: three attempts with waits
between them and one slow response take the same wall-clock shape. Two levels
separate them — the outer span is what reaching the provider cost in total, and
one child per attempt says whether that was one call or several.
"""

from __future__ import annotations

import httpx
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from dss.adapters.invocation.client import HttpCapabilityInvocation, SelectFailed
from dss.core.provider_discovery.models import ProviderCapability

CAPABILITY = ProviderCapability(
    provider_id="mausamgram",
    provider_name="IMD Mausamgram NWP",
    capability="openagrinet:WeatherObservation",
    resource_id="res:mausamgram:point-forecast",
    observed_categories=("Weather",),
)


@pytest.fixture
def spans(monkeypatch) -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr("opentelemetry.trace.get_tracer_provider", lambda: provider)
    return exporter


def _invocation(handler, **kwargs) -> HttpCapabilityInvocation:
    return HttpCapabilityInvocation(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        base_url="https://provider-network-vistaar.da.gov.in/oan",
        sender_id="seeker-network-vistaar.da.gov.in",
        receiver_id="provider-network-vistaar.da.gov.in",
        backoff_seconds=0.0,
        **kwargs,
    )


async def test_every_attempt_gets_its_own_span_under_the_call(spans) -> None:
    """Three failed attempts, not one slow call. Without the children the
    outer span's duration is the same either way, which is the confusion this
    nesting exists to remove."""

    def always_429(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "simulated"})

    with pytest.raises(SelectFailed):
        await _invocation(always_429).select(CAPABILITY, {}, "txn-1")

    finished = spans.get_finished_spans()
    attempts = [s for s in finished if s.name == "dss.select.attempt"]
    (call,) = [s for s in finished if s.name == "dss.select"]

    assert len(attempts) == 3
    assert {s.parent.span_id for s in attempts} == {call.context.span_id}
    assert call.status.status_code is StatusCode.ERROR


async def test_the_call_span_names_the_provider_it_reached(spans) -> None:
    """`dss.select` without this says only that a provider call took 3s, not
    which provider. The attributes are the capability and who serves it —
    never `resource_attributes`, which carry what the farmer asked for."""

    def answers(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message": {
                    "contract": {
                        "commitments": [
                            {
                                "resources": [
                                    {
                                        "id": "res:answered",
                                        "resourceAttributes": {
                                            "@type": CAPABILITY.capability,
                                            "parameters": [],
                                        },
                                    }
                                ]
                            }
                        ]
                    }
                }
            },
        )

    await _invocation(answers).select(CAPABILITY, {"commodity": "onion"}, "txn-1")

    call = next(s for s in spans.get_finished_spans() if s.name == "dss.select")
    assert call.attributes["provider_id"] == "mausamgram"
    assert "onion" not in str(call.attributes)


async def test_each_attempt_says_why_it_failed(spans) -> None:
    """The durations are the spans themselves and the retry count is how many
    there are. What the timeline cannot show is *why* each one failed — a 503
    and a 429 make identically shaped boxes.

    The status code only, never `SelectFailed.detail`: that carries the
    provider's response body, which echoes the farmer's query back."""

    codes = iter((503, 429, 500))

    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(next(codes), json={"error": "secret-query-echo"})

    with pytest.raises(SelectFailed):
        await _invocation(failing).select(CAPABILITY, {}, "txn-1")

    attempts = [s for s in spans.get_finished_spans() if s.name == "dss.select.attempt"]
    assert [s.attributes["http.status_code"] for s in attempts] == [503, 429, 500]
    assert "secret-query-echo" not in str([dict(s.attributes) for s in attempts])


async def test_the_call_span_counts_its_attempts(spans) -> None:
    """One number to alert on, rather than counting children by eye."""

    def always_429(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "simulated"})

    with pytest.raises(SelectFailed):
        await _invocation(always_429).select(CAPABILITY, {}, "txn-1")

    call = next(s for s in spans.get_finished_spans() if s.name == "dss.select")
    assert call.attributes["attempts"] == 3
