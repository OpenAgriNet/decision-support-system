"""Picks the redactor at boot: the real one when rules are loaded, the stub
when redaction is off."""

from __future__ import annotations

from dss.adapters.redaction.local import LocalRedactor
from dss.adapters.redaction.stub import StubRedactor
from dss.core.redaction.models import RedactionConfig
from dss.ports.redactor import Redactor


def build_redactor(config: RedactionConfig | None) -> Redactor:
    """``config`` is what ``load_redaction_config`` returned: ``None`` when
    ``DSS_REDACTION_ENABLED`` is off."""

    if config is None:
        return StubRedactor()
    return LocalRedactor(config)
