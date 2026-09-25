"""Tier 1 — every metric the dashboard queries is one the DSS publishes.

A renamed metric does not fail anything: its panel just goes empty. The
`first_claim` to `composed` rename did exactly that. This catches it here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from dss.adapters.observability.metrics import LABEL_KEYS

DASHBOARD = Path(__file__).parents[4] / "grafana" / "dashboards" / "dss.json"

# Published by the HTTP instrumentor, not by `metrics.py`.
HTTP_METRICS = {"http.server.request.duration"}


def _queried_metrics() -> set[str]:
    panels = json.loads(DASHBOARD.read_text())["panels"]
    return {
        name
        for panel in panels
        for target in panel.get("targets", [])
        for name in re.findall(r"MetricName = '([^']+)'", target.get("rawSql", ""))
    }


def test_the_dashboard_queries_only_published_metrics() -> None:
    queried = _queried_metrics()

    assert queried, "no MetricName found — has the query format changed?"
    assert queried <= set(LABEL_KEYS) | HTTP_METRICS
