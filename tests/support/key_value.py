from __future__ import annotations

from collections import defaultdict
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import anyio
import anyio.lowlevel

from dss.ports.key_value_store import KeyValueStore, LockAcquireTimeoutError


class FakeKeyValueStore(KeyValueStore):
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.set_calls: list[str] = []
        self.locked_keys: list[str] = []
        # A semaphore, not anyio.Lock: the port's lock is ownerless, so another
        # task may release it.
        self._locks: defaultdict[str, anyio.Semaphore] = defaultdict(
            lambda: anyio.Semaphore(1)
        )

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, *, ttl_seconds: int) -> None:
        self.set_calls.append(key)
        self.values[key] = value

    @asynccontextmanager
    async def lock(
        self, key: str, *, lease_seconds: float, acquire_timeout_seconds: float
    ) -> AsyncGenerator[None]:
        if lease_seconds <= 0:
            raise ValueError(f"lease_seconds must be above 0, got {lease_seconds}")
        semaphore = self._locks[key]
        try:
            with anyio.fail_after(acquire_timeout_seconds):
                await semaphore.acquire()
        except TimeoutError:
            raise LockAcquireTimeoutError(key) from None
        self.locked_keys.append(key)
        try:
            yield
        finally:
            await self._release(semaphore)

    async def _release(self, semaphore: anyio.Semaphore) -> None:
        # Awaits like a network release does, so a cancelled caller is cancelled here.
        await anyio.lowlevel.checkpoint()
        semaphore.release()

    def is_locked(self, key: str) -> bool:
        return self._locks[key].value == 0


class FailingReadStore(FakeKeyValueStore):
    async def get(self, key: str) -> str | None:
        raise ConnectionError("store down")


class FailingSaveStore(FakeKeyValueStore):
    async def set(self, key: str, value: str, *, ttl_seconds: int) -> None:
        raise ConnectionError("store down")


class FailingReleaseStore(FakeKeyValueStore):
    async def _release(self, semaphore: anyio.Semaphore) -> None:
        semaphore.release()
        raise ConnectionError("store down")


class HangingStore(FakeKeyValueStore):
    # Hangs until the test lets go, so a missing deadline fails rather than hangs.
    def __init__(self) -> None:
        super().__init__()
        self.let_go = anyio.Event()


class HangingReadStore(HangingStore):
    def __init__(self) -> None:
        super().__init__()
        self.reading = anyio.Event()

    async def get(self, key: str) -> str | None:
        self.reading.set()
        await self.let_go.wait()
        return None


class HangingSaveStore(HangingStore):
    async def set(self, key: str, value: str, *, ttl_seconds: int) -> None:
        await self.let_go.wait()


class HangingReleaseStore(HangingStore):
    async def _release(self, semaphore: anyio.Semaphore) -> None:
        await self.let_go.wait()
        semaphore.release()


class UnreachableStore(FakeKeyValueStore):
    async def get(self, key: str) -> str | None:
        raise ConnectionError("store down")

    async def set(self, key: str, value: str, *, ttl_seconds: int) -> None:
        raise ConnectionError("store down")

    @asynccontextmanager
    async def lock(
        self, key: str, *, lease_seconds: float, acquire_timeout_seconds: float
    ) -> AsyncGenerator[None]:
        raise ConnectionError("store down")
        yield
