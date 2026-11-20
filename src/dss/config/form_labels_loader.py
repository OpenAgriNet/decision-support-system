"""Loads the labels a farmer reads on a form's fields.

Mirrors ``clarification_text_loader.py``: a configured-but-missing path raises
rather than silently falling back to a different configuration.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from dss.core.forms.models import FormLabels

_DEFAULTS = Path(__file__).parent / "defaults" / "form-labels.yaml"


def load_form_labels(path: Path | None = None) -> FormLabels:
    """Load the form labels.

    - ``path`` unset → bundled defaults.
    - ``path`` set but missing → ``FileNotFoundError``.
    """

    source = _DEFAULTS if path is None else path
    if not source.exists():
        raise FileNotFoundError(
            f"form labels config path is set to {source} but no file is there "
            "— refusing to boot on a different configuration"
        )
    by_scheme = yaml.safe_load(source.read_text(encoding="utf-8"))
    return FormLabels(by_scheme=by_scheme or {})
