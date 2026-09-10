"""Telemetry backed by a vendor SDK.

Here rather than in `dss/observability/` because this imports Pydantic AI and
logfire, and `adapters/` is the one place outside `orchestration/` where
vendor SDK code is allowed (ADR-0001 §4.3). `dss/observability/trace_log.py`
stays where it is — stdlib logging, no framework.
"""
