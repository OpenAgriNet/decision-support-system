"""Trace logging: component entry/exit and external-service responses.

One place so the format is uniform and greppable. Every line carries a
``request_id`` — the turn's ``transactionId``, which is also its ``traceId`` —
so a whole turn's path across components and services joins on one key
(``grep "request_id=<id>"``).

Two things get logged:

- **Component span** — ``trace_component(name)`` logs ``event=enter`` on the way
  in and ``event=exit`` on the way out, with ``elapsed_ms`` and whether it left
  by returning or raising. Wrap the call at its orchestration seam, so ``core/``
  services stay logging-free and pure.
- **External response** — ``log_external_response(service, ...)`` logs what a
  provider or model handed back. The user asked for *every* external response,
  so this is called at each LLM and network call site.

Everything goes to the ``dss.trace`` logger at INFO; raise or lower that one
logger to tune verbosity without touching the rest of the app. Bodies can be
large and may contain farmer-facing content — see CONVENTIONS.md on PII before
shipping these lines to a broad-retention sink.

``request_id`` need not be passed everywhere: it is stashed in a context
variable at the top of the turn (``bind_request_id``), so an adapter with no
``transaction_id`` in hand (the LLM provider port takes none) still tags its
line correctly. Anyio copies the context into each child task, so the id set
before the intent/moderation task group reaches both branches.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

logger = logging.getLogger("dss.trace")

_request_id: ContextVar[str] = ContextVar("dss_request_id", default="-")

# Bodies over this are clipped so one line stays readable; the clip length is
# generous because the whole point here is to see what came back.
_MAX_BODY = 8000


def bind_request_id(request_id: str) -> None:
    """Make ``request_id`` the ambient id for everything downstream in this
    turn's context (and its child tasks). Called once at the top of a turn."""

    _request_id.set(request_id)


def current_request_id() -> str:
    return _request_id.get()


def _rid(request_id: str | None) -> str:
    return request_id if request_id is not None else _request_id.get()


@contextmanager
def trace_component(component: str, request_id: str | None = None) -> Iterator[None]:
    """Log ``enter`` before the wrapped work and ``exit`` after — with the
    elapsed time and whether it returned or raised. Wrap an ``await`` directly:

        with trace_component("intent", turn.transaction_id):
            intent = await classify_intent(turn, llm)
    """

    rid = _rid(request_id)
    start = time.monotonic()
    logger.info("request_id=%s component=%s event=enter", rid, component)
    status = "ok"
    try:
        yield
    except BaseException as exc:  # noqa: BLE001 - re-raised; we only tag the span
        status = f"error:{type(exc).__name__}"
        raise
    finally:
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info(
            "request_id=%s component=%s event=exit status=%s elapsed_ms=%.1f",
            rid,
            component,
            status,
            elapsed_ms,
        )


def log_external_response(
    service: str,
    request_id: str | None = None,
    *,
    status: Any = None,
    body: Any = None,
    **fields: Any,
) -> None:
    """Log one response from an external service (a model or a network call).

    ``service`` names it (``llm``, ``llm.planner``, ``discovery``,
    ``invocation``); ``status`` is an HTTP code or a short tag; ``body`` is the
    payload, rendered to text and clipped. Extra ``**fields`` are appended as
    ``key=value`` for anything worth pinning next to it (schema, capability).
    """

    parts = [f"request_id={_rid(request_id)}", f"external={service}", "event=response"]
    if status is not None:
        parts.append(f"status={status}")
    parts.extend(f"{key}={value}" for key, value in fields.items())
    if body is not None:
        parts.append(f"body={_as_text(body)}")
    logger.info(" ".join(parts))


def _as_text(body: Any) -> str:
    if isinstance(body, (dict, list)):
        try:
            text = json.dumps(body, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = repr(body)
    else:
        text = str(body)
    if len(text) > _MAX_BODY:
        dropped = len(text) - _MAX_BODY
        return f"{text[:_MAX_BODY]}...<+{dropped} chars clipped>"
    return text
