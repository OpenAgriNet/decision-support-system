from __future__ import annotations

from collections.abc import Callable
from typing import Any

import anyio
import httpx

from dss.adapters.observability.tracing import open_span
from dss.observability.trace_log import log_event

type RetryPredicate = Callable[[httpx.HTTPError], bool]


class HttpClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        base_url: str,
        backoff_seconds: float = 0.5,
    ) -> None:
        self._client = client
        self._base_url = base_url
        self._backoff_seconds = backoff_seconds

    async def post(
        self,
        path: str,
        *,
        json: dict[str, Any],
        retries: int = 0,
        should_retry: RetryPredicate | None = None,
    ) -> httpx.Response:
        url = f"{self._base_url}/{path}"
        for attempt in range(1, retries + 2):
            try:
                return await self._post_once(url, json=json)
            except httpx.HTTPError as error:
                if not _is_retried(
                    error, attempt=attempt, retries=retries, should_retry=should_retry
                ):
                    raise
                _log_retry(path, error, attempt=attempt)
                # Doubling, so a struggling server is not hammered.
                await anyio.sleep(self._backoff_seconds * 2 ** (attempt - 1))
        raise AssertionError("unreachable: the loop either returns or raises")

    async def _post_once(self, url: str, *, json: dict[str, Any]) -> httpx.Response:
        with open_span("dss.http.post") as span:
            response = await self._client.post(url, json=json)
            # The code only, never the body: it can echo the farmer's words.
            span.set_attribute("http.status_code", response.status_code)
            response.raise_for_status()
            return response


# No rule, no retry: a caller that sets `retries` alone never resends.
def _is_retried(
    error: httpx.HTTPError,
    *,
    attempt: int,
    retries: int,
    should_retry: RetryPredicate | None,
) -> bool:
    return attempt <= retries and should_retry is not None and should_retry(error)


def _log_retry(path: str, error: httpx.HTTPError, *, attempt: int) -> None:
    log_event(
        "http",
        event="retry",
        method="POST",
        path=path,
        status=_status_of(error),
        attempt=attempt,
    )


def _status_of(error: httpx.HTTPError) -> int | str:
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code
    return type(error).__name__
