"""Prints a log line's attributes as `key=value` on the console.

- Trace lines keep their fields as attributes, so a log search can filter them.
- On the console those fields would vanish; this puts them back, greppable.
"""

from __future__ import annotations

import logging

from dss.observability.trace_log import current_otel_ids

# What every record has before `extra=` adds to it.
_STANDARD_KEYS = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class KeyValueFormatter(logging.Formatter):
    """The base line, then each attribute, then the active span's ids.

    The span ids are read when the line is written, so call this from the
    handler that writes it, not later.
    """

    def format(self, record: logging.LogRecord) -> str:
        fields = [*_attributes_of(record), *_span_ids()]
        return " ".join([super().format(record), *fields])


def _attributes_of(record: logging.LogRecord) -> list[str]:
    return [
        f"{key}={value}"
        for key, value in vars(record).items()
        if key not in _STANDARD_KEYS
    ]


def _span_ids() -> list[str]:
    trace_id, span_id = current_otel_ids()
    return [f"trace_id={trace_id}", f"span_id={span_id}"]
