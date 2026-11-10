from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from contextlib import suppress

import pytest
from docker.errors import DockerException
from redis.asyncio import Redis
from testcontainers.community.redis import RedisContainer
from testcontainers.core.config import testcontainers_config

from dss.adapters.key_value.redis import RedisKeyValueStore

# Ryuk cannot mount the Podman socket on a Mac, and the fixture stops the container.
testcontainers_config.ryuk_disabled = True


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    container = RedisContainer("redis:7")
    try:
        container.start()
    except Exception as error:
        # start() can create the container and then fail, and Ryuk is off.
        with suppress(Exception):
            container.stop()
        # CI must run these; only a laptop without Docker may skip them.
        if not isinstance(error, DockerException) or os.environ.get("CI"):
            raise
        pytest.skip(f"no Docker for the Redis container: {error}")
    try:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"
    finally:
        container.stop()


@pytest.fixture
async def redis_client(redis_url: str) -> AsyncIterator[Redis]:
    client = Redis.from_url(redis_url, decode_responses=True)
    await client.flushdb()
    yield client
    await client.aclose()


@pytest.fixture
async def store(
    redis_client: Redis, redis_url: str
) -> AsyncIterator[RedisKeyValueStore]:
    store = RedisKeyValueStore(redis_url)
    yield store
    await store.aclose()
