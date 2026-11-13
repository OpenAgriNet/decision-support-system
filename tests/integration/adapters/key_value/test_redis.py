from __future__ import annotations

import anyio
import pytest
from redis.asyncio import Redis

from dss.adapters.key_value.redis import RedisKeyValueStore
from dss.ports.key_value_store import LockAcquireTimeoutError

_KEY = "sender:session"


async def test_a_value_is_read_back(store: RedisKeyValueStore) -> None:
    await store.set(_KEY, "state", ttl_seconds=180)

    assert await store.get(_KEY) == "state"


async def test_a_missing_key_reads_as_none(store: RedisKeyValueStore) -> None:
    assert await store.get(_KEY) is None


async def test_a_value_is_stored_with_its_ttl(
    store: RedisKeyValueStore, redis_client: Redis
) -> None:
    await store.set(_KEY, "state", ttl_seconds=180)

    assert 0 < await redis_client.ttl(_KEY) <= 180


async def test_a_value_is_gone_once_its_ttl_passes(store: RedisKeyValueStore) -> None:
    await store.set(_KEY, "state", ttl_seconds=1)

    await anyio.sleep(1.1)

    assert await store.get(_KEY) is None


async def test_a_value_and_its_lock_on_one_key_do_not_overwrite_each_other(
    store: RedisKeyValueStore,
) -> None:
    async with store.lock(_KEY, lease_seconds=5, acquire_timeout_seconds=1):
        await store.set(_KEY, "state", ttl_seconds=180)
        with pytest.raises(LockAcquireTimeoutError):
            async with store.lock(_KEY, lease_seconds=5, acquire_timeout_seconds=0.2):
                pass

    assert await store.get(_KEY) == "state"


async def test_two_holders_of_one_key_run_one_after_the_other(
    store: RedisKeyValueStore,
) -> None:
    steps: list[str] = []

    async def hold(name: str) -> None:
        async with store.lock(_KEY, lease_seconds=5, acquire_timeout_seconds=5):
            steps.append(f"{name} in")
            await anyio.sleep(0.2)
            steps.append(f"{name} out")

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(hold, "first")
        await anyio.sleep(0.05)
        tasks.start_soon(hold, "second")

    assert steps == ["first in", "first out", "second in", "second out"]


async def test_two_keys_do_not_wait_on_each_other(store: RedisKeyValueStore) -> None:
    async with (
        store.lock("a", lease_seconds=5, acquire_timeout_seconds=1),
        store.lock("b", lease_seconds=5, acquire_timeout_seconds=0.2),
    ):
        pass


async def test_waiting_past_the_acquire_timeout_raises(
    store: RedisKeyValueStore,
) -> None:
    async with store.lock(_KEY, lease_seconds=5, acquire_timeout_seconds=1):
        with pytest.raises(LockAcquireTimeoutError):
            async with store.lock(_KEY, lease_seconds=5, acquire_timeout_seconds=0.2):
                pass


async def test_a_lock_released_from_another_task_can_be_taken_again(
    store: RedisKeyValueStore,
) -> None:
    held = store.lock(_KEY, lease_seconds=5, acquire_timeout_seconds=1)
    await held.__aenter__()

    async def release() -> None:
        await held.__aexit__(None, None, None)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(release)

    async with store.lock(_KEY, lease_seconds=5, acquire_timeout_seconds=0.2):
        pass


async def test_a_holder_past_its_lease_no_longer_blocks_the_next(
    store: RedisKeyValueStore,
) -> None:
    stuck = store.lock(_KEY, lease_seconds=0.2, acquire_timeout_seconds=1)
    await stuck.__aenter__()

    async with store.lock(_KEY, lease_seconds=5, acquire_timeout_seconds=1):
        pass


async def test_a_late_release_does_not_free_the_next_holders_lock(
    store: RedisKeyValueStore,
) -> None:
    late = store.lock(_KEY, lease_seconds=0.2, acquire_timeout_seconds=1)
    await late.__aenter__()
    await anyio.sleep(0.3)
    current = store.lock(_KEY, lease_seconds=5, acquire_timeout_seconds=1)
    await current.__aenter__()

    await late.__aexit__(None, None, None)

    with pytest.raises(LockAcquireTimeoutError):
        async with store.lock(_KEY, lease_seconds=5, acquire_timeout_seconds=0.2):
            pass


@pytest.mark.parametrize("lease_seconds", [0, -1])
async def test_a_lease_of_zero_or_less_is_refused(
    store: RedisKeyValueStore, redis_client: Redis, lease_seconds: float
) -> None:
    with pytest.raises(ValueError, match="lease_seconds"):
        async with store.lock(
            _KEY, lease_seconds=lease_seconds, acquire_timeout_seconds=1
        ):
            pass

    assert await redis_client.keys() == []
