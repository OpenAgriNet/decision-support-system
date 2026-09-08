"""Reading the request body without letting it decide how much memory we use.

A status-code test is not enough here. `gzip.decompress` on a bomb returns the
right `413` *after* allocating the whole payload, so these tests assert the
source stream is **not fully consumed** — which is the only observable difference
between aborting early and inflating first.
"""

from __future__ import annotations

import gzip
from collections.abc import AsyncIterator

import pytest

from dss.adapters.http.v1.router import _decoded_body, _TooLarge, _Undecodable

CAP = 1_000


class CountingStream:
    """Yields chunks and records how many were actually pulled."""

    def __init__(self, payload: bytes, *, chunk: int = 64) -> None:
        self._chunks = [payload[i : i + chunk] for i in range(0, len(payload), chunk)]
        self.consumed = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for piece in self._chunks:
            self.consumed += 1
            yield piece

    @property
    def total(self) -> int:
        return len(self._chunks)


async def test_a_plain_body_within_the_cap_is_returned():
    stream = CountingStream(b'{"a": 1}')

    assert (
        await _decoded_body(stream, encoding=None, cap=CAP, declared=None)
        == b'{"a": 1}'
    )


async def test_a_gzipped_body_within_the_cap_is_inflated():
    payload = b'{"a": 1}'
    stream = CountingStream(gzip.compress(payload))

    assert (
        await _decoded_body(stream, encoding="gzip", cap=CAP, declared=None) == payload
    )


async def test_a_declared_length_over_the_cap_is_refused_before_reading():
    """The cheapest possible rejection: `Content-Length` says it is too big, so
    the body is never pulled at all."""

    stream = CountingStream(b"x" * 10_000)

    with pytest.raises(_TooLarge):
        await _decoded_body(stream, encoding=None, cap=CAP, declared=10_000)

    assert stream.consumed == 0


async def test_a_plain_body_over_the_cap_stops_early():
    stream = CountingStream(b"x" * 100_000)

    with pytest.raises(_TooLarge):
        await _decoded_body(stream, encoding=None, cap=CAP, declared=None)

    assert stream.consumed < stream.total, "read the whole oversized body"


async def test_a_decompression_bomb_stops_early():
    """A few KB of gzip that inflates to megabytes. The old code called
    `gzip.decompress` and allocated all of it before checking the size; this
    asserts the compressed stream is abandoned instead."""

    bomb = gzip.compress(b"\0" * 20_000_000)
    stream = CountingStream(bomb)

    with pytest.raises(_TooLarge):
        await _decoded_body(stream, encoding="gzip", cap=CAP, declared=None)

    assert stream.consumed < stream.total, "consumed the whole bomb"


async def test_a_body_that_is_not_gzip_is_undecodable():
    stream = CountingStream(b"plainly not gzip")

    with pytest.raises(_Undecodable):
        await _decoded_body(stream, encoding="gzip", cap=CAP, declared=None)


async def test_a_truncated_gzip_body_is_undecodable():
    packed = gzip.compress(b'{"a": 1}')
    stream = CountingStream(packed[: len(packed) // 2])

    with pytest.raises(_Undecodable):
        await _decoded_body(stream, encoding="gzip", cap=CAP, declared=None)


async def test_an_unknown_content_encoding_is_left_alone():
    """Only gzip is decoded. Anything else passes through rather than being
    guessed at, so a body that is really JSON still works."""

    stream = CountingStream(b'{"a": 1}')

    result = await _decoded_body(stream, encoding="identity", cap=CAP, declared=None)

    assert result == b'{"a": 1}'


async def test_an_oversized_compressed_stream_stops_early():
    """Incompressible noise: the *output* stays under the cap, so only a cap on
    bytes read stops it. Without one, a caller could stream gigabytes."""

    # gzip container, compresslevel 0: a real gzip stream that stores its input
    # verbatim, so the output never shrinks below the cap on its own.
    noise = gzip.compress(bytes(range(256)) * 200_000, compresslevel=0)
    stream = CountingStream(noise)

    with pytest.raises(_TooLarge):
        await _decoded_body(stream, encoding="gzip", cap=CAP, declared=None)

    assert stream.consumed < stream.total
