"""A stand-in for the OAN network, so a local run can answer from a provider.

Two routes, `POST /discover` and `POST /select`, speaking the same synchronous
request/response contract the real Network Adapter does — no callbacks, no
auth. Run it beside the DSS:

    uv run python -m tools.mock_network --port 8078

Not part of the package: `pyproject.toml` ships only `src/dss`. This is a dev
tool, and it is deliberately outside `tests/` too, because the point is to run
it by hand and watch a real turn come back.
"""
