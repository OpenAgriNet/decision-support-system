"""HttpCapabilityInvocation against a real local HTTP server.

Complements test_failure_classification.py's MockTransport tests: this
proves an actual socket-level round trip works — something an in-process
mock transport cannot prove, since it never leaves the process.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx2
import pytest
from pytest_httpserver import HTTPServer

from dss.adapters.invocation.client import HttpCapabilityInvocation, SelectFailed
from dss.core.provider_discovery.models import ProviderCapability

FIXTURES = Path(__file__).parent / "fixtures"

CAPABILITY = ProviderCapability(
    provider_id="mausamgram",
    provider_name="IMD Mausamgram NWP",
    capability="openagrinet:WeatherObservation",
    resource_id="res:mausamgram:point-forecast",
    observed_categories=("Weather",),
)


def _base_url(httpserver: HTTPServer) -> str:
    return httpserver.url_for("").rstrip("/")


async def test_select_against_a_real_local_server(httpserver: HTTPServer) -> None:
    select_response = json.loads((FIXTURES / "select_response.json").read_text())
    httpserver.expect_request("/select", method="POST").respond_with_json(
        select_response
    )

    async with httpx2.AsyncClient() as client:
        invocation = HttpCapabilityInvocation(
            client=client,
            base_url=_base_url(httpserver),
            sender_id="seeker-network-vistaar.da.gov.in",
            receiver_id="provider-network-vistaar.da.gov.in",
        )

        answer = await invocation.select(
            CAPABILITY,
            {"@type": "openagrinet:WeatherObservation"},
            transaction_id="txn-test",
        )

    assert answer.provider_id == "mausamgram"
    assert answer.resource_id == "res:mausamgram:forecast:2026-08-26"


async def test_a_real_429_response_raises_selectfailed(httpserver: HTTPServer) -> None:
    httpserver.expect_request("/select", method="POST").respond_with_json(
        {"error": "rate limited"}, status=429
    )

    async with httpx2.AsyncClient() as client:
        invocation = HttpCapabilityInvocation(
            client=client,
            base_url=_base_url(httpserver),
            sender_id="seeker-network-vistaar.da.gov.in",
            receiver_id="provider-network-vistaar.da.gov.in",
            attempts=1,  # classification, not retry — see test_retry.py
        )

        with pytest.raises(SelectFailed) as exc_info:
            await invocation.select(CAPABILITY, {}, transaction_id="txn-test")

    assert exc_info.value.status_code == 429
