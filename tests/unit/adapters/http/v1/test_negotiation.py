"""`Accept` parsing, as a pure function.

Driven directly because a test client always sends *some* `Accept`, so the
absent case is unreachable through HTTP.
"""

from __future__ import annotations

import pytest

from dss.adapters.http.v1.router import _wants_stream


@pytest.mark.parametrize("accept", [None, "", "application/json", "*/*", "application/*"])
def test_json_is_the_default_and_the_fallback(accept):
    assert _wants_stream(accept) is False


@pytest.mark.parametrize(
    "accept",
    [
        "text/event-stream",
        "text/event-stream, application/json",
        "application/json, text/event-stream;q=0.9",
        " text/event-stream ",
    ],
)
def test_the_event_stream_is_taken_whenever_it_is_offered(accept):
    assert _wants_stream(accept) is True


@pytest.mark.parametrize("accept", ["application/xml", "text/plain", "image/png"])
def test_anything_else_cannot_be_served(accept):
    assert _wants_stream(accept) is None
