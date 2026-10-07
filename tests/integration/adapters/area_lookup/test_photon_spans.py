"""Tier 2 — the span a Photon call leaves behind.

A place that is "not found" can mean Photon does not know it, or Photon was
down. The span is what tells the two apart. It must not carry the place name:
traces are somewhere personal data may not go.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from dss.adapters.area_lookup.photon import PhotonAreaLookup
from dss.ports.area_lookup import AreaLookupUnavailable

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def spans(monkeypatch) -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr("opentelemetry.trace.get_tracer_provider", lambda: provider)
    return exporter


def _photon_returning(fixture: str) -> PhotonAreaLookup:
    body = (FIXTURES / fixture).read_bytes()
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    )
    return PhotonAreaLookup(
        client=client,
        base_url="http://photon.test",
        country_codes=("KE",),
        timeout_seconds=5.0,
    )


async def test_a_failed_call_is_an_error(spans) -> None:
    """The span is the only place a Photon outage shows. It must say `error`
    and be marked failed, not look like an empty answer."""

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500))
    )
    photon = PhotonAreaLookup(
        client=client,
        base_url="http://photon.test",
        country_codes=("KE",),
        timeout_seconds=5.0,
    )

    with pytest.raises(AreaLookupUnavailable):
        await photon.resolve("Eldoret")

    (span,) = spans.get_finished_spans()
    assert span.attributes["outcome"] == "error"
    assert span.status.status_code == StatusCode.ERROR


async def test_a_miss_is_not_a_hit(spans) -> None:
    """Photon answered and knows no such place. Not the same as being down."""

    photon = _photon_returning("photon_miss.json")

    await photon.resolve("Zzqxvk")

    (span,) = spans.get_finished_spans()
    assert span.attributes["result_count"] == 0
    assert span.attributes["outcome"] == "miss"


async def test_a_hit_leaves_a_span_without_the_name(spans) -> None:
    photon = _photon_returning("photon_eldoret.json")

    await photon.resolve("Eldoret")

    (span,) = spans.get_finished_spans()
    assert span.name == "dss.area_lookup.photon"
    assert span.attributes["result_count"] == 1
    assert span.attributes["outcome"] == "hit"
    assert span.attributes["name_length"] == len("Eldoret")
    assert span.attributes["country_codes"] == "KE"
    assert not any("Eldoret" in str(value) for value in span.attributes.values())
