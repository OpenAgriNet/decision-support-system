"""Framework-agnostic domain logic — plain Python in, plain Python out.

Nothing here may import the agentic framework (``pydantic_ai`` or
``pydantic_graph``) — a hard boundary. Core services depend only on
``dss.ports`` and ``dss.core.models``, which keeps them unit-testable without a
framework runtime and confines a future framework swap to ``dss.orchestration``.

Enforced two ways: ruff's TID251 ban, and a test that walks the AST of every
core module (ruff's ban is bypassable via ``importlib``, so it is a guardrail
rather than a boundary on its own).
"""
