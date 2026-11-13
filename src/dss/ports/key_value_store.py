from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Protocol


class LockAcquireTimeoutError(Exception):
    pass


class KeyValueStore(Protocol):
    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str, *, ttl_seconds: int) -> None: ...

    # Ownerless: a closed stream is finalised, and the lock released, in another task.
    # Past lease_seconds the next caller takes it; a release after that is ignored.
    # Raises LockAcquireTimeoutError once acquire_timeout_seconds pass, and
    # ValueError for a lease_seconds of 0 or less.
    def lock(
        self, key: str, *, lease_seconds: float, acquire_timeout_seconds: float
    ) -> AbstractAsyncContextManager[None]: ...
