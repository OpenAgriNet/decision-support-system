"""Tier 1 — what tracing sends, and what it must not.

`Agent.instrument_all()` puts the farmer's raw query and the composed answer
into every span by default. `DSS_ARCHITECTURE.md` §6.1 forbids exactly that:
"prompts containing personal data" and "traces" are both named. So the
settings this builds are the contract, and these tests are what hold it.
"""

from __future__ import annotations

import pytest

from dss.adapters.observability.tracing import instrumentation_settings, tracing_enabled


def test_message_content_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """The one rule the architecture doc states outright.

    A span carrying `gen_ai.input.messages` with real text is a prompt logged
    to a trace backend, which §6.1 does not permit. Roles, token counts and
    latency survive — which is what tracing is for here.
    """

    monkeypatch.delenv("DSS_TRACE_INCLUDE_MESSAGE_CONTENT", raising=False)

    assert instrumentation_settings().include_content is False


def test_content_is_included_only_when_explicitly_asked_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local debugging sometimes needs the prompt, so there is a way in.

    Named for what it does rather than something vague like `DSS_TRACE_DEBUG`,
    because the cost of setting it is a farmer's words leaving the process.
    """

    monkeypatch.setenv("DSS_TRACE_INCLUDE_MESSAGE_CONTENT", "true")

    assert instrumentation_settings().include_content is True


def test_anything_other_than_true_leaves_content_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only an explicit `true` opts in.

    `1`, `yes` and an empty value all read as off. A permissive parser here
    would mean a typo in a deployment's env turning message content on.
    """

    for value in ("1", "yes", "TRUE ", "", "false"):
        monkeypatch.setenv("DSS_TRACE_INCLUDE_MESSAGE_CONTENT", value)
        assert instrumentation_settings().include_content is False, value


def test_tracing_is_off_without_an_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """No endpoint means no tracing, and no error.

    A local run, and every test, has none — so the absence has to be the
    quiet path rather than something to configure around.
    """

    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

    assert tracing_enabled() is False


def test_an_endpoint_turns_tracing_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")

    assert tracing_enabled() is True
