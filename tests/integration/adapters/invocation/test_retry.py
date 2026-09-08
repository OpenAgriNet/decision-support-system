"""Contract tests for retrying a failed /select call.

Transient failures are retried; defects are not — retrying a 400 sends the
same malformed request again and only delays the failure.

Retries live here, in the adapter, not in the tool: ``ModelRetry`` would hand
control back to the model, which burns its retry budget and invites it to
change arguments that were never the problem. The model sees one answer or
one failure.
"""

from __future__ import annotations

import httpx2
import pytest

from dss.adapters.invocation.client import HttpCapabilityInvocation, SelectFailed
from dss.core.provider_discovery.models import ProviderCapability

CAPABILITY = ProviderCapability(
    provider_id="mausamgram",
    provider_name="IMD Mausamgram NWP",
    capability="openagrinet:WeatherObservation",
    resource_id="res:mausamgram:point-forecast",
    observed_categories=("Weather",),
)


_ANSWERED_RESOURCE_ID = "res:mausamgram:point-forecast:2026-08-26"


class _CountingHandler:
    """Fails with ``status_code`` for the first ``fail_times`` calls, then
    succeeds. Records how many times it was called."""

    def __init__(self, status_code: int, *, fail_times: int) -> None:
        self._status_code = status_code
        self._fail_times = fail_times
        self.calls = 0

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.calls += 1
        if self.calls <= self._fail_times:
            return httpx2.Response(self._status_code, json={"error": "simulated"})
        return httpx2.Response(
            200,
            json={
                "message": {
                    "contract": {
                        "commitments": [
                            {
                                "resources": [
                                    {
                                        "id": _ANSWERED_RESOURCE_ID,
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


def _invocation(handler: _CountingHandler, **kwargs) -> HttpCapabilityInvocation:
    return HttpCapabilityInvocation(
        client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        base_url="https://provider-network-vistaar.da.gov.in/oan",
        sender_id="seeker-network-vistaar.da.gov.in",
        receiver_id="provider-network-vistaar.da.gov.in",
        backoff_seconds=0.0,  # keep the test fast; spacing is tested separately
        **kwargs,
    )


async def test_a_transient_failure_is_retried_until_it_succeeds() -> None:
    handler = _CountingHandler(429, fail_times=2)

    answer = await _invocation(handler).select(CAPABILITY, {}, "txn-1")

    assert handler.calls == 3
    assert answer.provider_id == CAPABILITY.provider_id


async def test_a_transient_failure_gives_up_after_the_configured_attempts() -> None:
    handler = _CountingHandler(429, fail_times=99)

    with pytest.raises(SelectFailed):
        await _invocation(handler).select(CAPABILITY, {}, "txn-1")

    assert handler.calls == 3  # the default


async def test_the_attempt_count_is_configurable() -> None:
    handler = _CountingHandler(429, fail_times=99)

    with pytest.raises(SelectFailed):
        await _invocation(handler, attempts=2).select(CAPABILITY, {}, "txn-1")

    assert handler.calls == 2


async def test_a_defect_is_not_retried() -> None:
    """A 400 means our request is malformed. Sending it twice more changes
    nothing and delays the failure the caller needs to see."""

    handler = _CountingHandler(400, fail_times=99)

    with pytest.raises(SelectFailed):
        await _invocation(handler).select(CAPABILITY, {}, "txn-1")

    assert handler.calls == 1
