from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import cast

from redis.asyncio import Redis
from redis.exceptions import LockNotOwnedError

from dss.ports.key_value_store import KeyValueStore, LockAcquireTimeoutError

# The lock is a Redis key of its own; without a prefix, set() would overwrite it.
_LOCK_KEY_PREFIX = "lock:"
# A Redis that does not answer must fail fast: callers fall back without it.
_SOCKET_CONNECT_TIMEOUT_SECONDS = 3
_SOCKET_TIMEOUT_SECONDS = 3


class RedisKeyValueStore(KeyValueStore):
    def __init__(self, url: str) -> None:
        self._client = Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=_SOCKET_CONNECT_TIMEOUT_SECONDS,
            socket_timeout=_SOCKET_TIMEOUT_SECONDS,
        )

    async def get(self, key: str) -> str | None:
        # decode_responses=True gives str; redis-py's types still say bytes or str.
        return cast(str | None, await self._client.get(key))

    async def set(self, key: str, value: str, *, ttl_seconds: int) -> None:
        await self._client.set(key, value, ex=ttl_seconds)

    @asynccontextmanager
    async def lock(
        self, key: str, *, lease_seconds: float, acquire_timeout_seconds: float
    ) -> AsyncIterator[None]:
        _require_positive_lease(lease_seconds)
        redis_lock = self._client.lock(
            f"{_LOCK_KEY_PREFIX}{key}",
            timeout=lease_seconds,
            blocking_timeout=acquire_timeout_seconds,
            # The token lives on the lock object, so any task may release it.
            thread_local=False,
        )
        if not await redis_lock.acquire():
            raise LockAcquireTimeoutError(key)
        try:
            yield
        finally:
            # Past lease_seconds the key holds the next holder's token, or none.
            with suppress(LockNotOwnedError):
                await redis_lock.release()

    async def aclose(self) -> None:
        await self._client.aclose()


def _require_positive_lease(lease_seconds: float) -> None:
    # redis-py reads a lease of 0 as "never expires"; a lease exists to rule that out.
    if lease_seconds <= 0:
        raise ValueError(f"lease_seconds must be above 0, got {lease_seconds}")
